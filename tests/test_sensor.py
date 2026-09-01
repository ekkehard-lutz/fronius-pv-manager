"""Tests for catalog-backed Home Assistant sensor entities."""

import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import EntityCategory

from custom_components.fronius_pv_manager.coordinator import (
    DecodedModelSnapshot,
    DeviceSnapshot,
    FroniusPVCoordinator,
    FroniusPVCoordinatorData,
)
from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    DiscoveredModel,
    EntityPlatform,
    PhysicalDeviceRole,
    SunSpecModelDefinition,
)
from custom_components.fronius_pv_manager.register_maps import (
    MODEL_1,
    MODEL_103,
    MODEL_120,
    MODEL_121,
    MODEL_122,
    MODEL_123,
    MODEL_124,
    MODEL_160,
    MODEL_203,
    MODEL_DEFINITIONS_BY_ID,
)
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    async_setup_entry,
)
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport


def _snapshot(
    definition: SunSpecModelDefinition,
    payload: Iterable[int] | None = None,
    *,
    base_address: int = 40000,
) -> DecodedModelSnapshot:
    """Create one decoded coordinator snapshot for a known definition."""
    length = definition.expected_length
    assert length is not None
    words = tuple(payload) if payload is not None else (0,) * length
    return DecodedModelSnapshot(
        discovered=DiscoveredModel(definition.model_ids[0], length, base_address),
        definition=definition,
        decoded=decode_model(definition, words),
    )


def _coordinator_with(
    *snapshots: DecodedModelSnapshot,
) -> tuple[FroniusPVCoordinator, FakeEntry, FakeTransport]:
    """Build a coordinator whose latest data consists of the snapshots."""
    hass = FakeHass()
    entry = FakeEntry({})
    transport = FakeTransport({})
    coordinator = FroniusPVCoordinator(hass, entry, {1: transport})
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            DeviceSnapshot(
                device_id=1,
                discovered_models=tuple(
                    snapshot.discovered for snapshot in snapshots
                ),
                decoded_models=snapshots,
            ),
        ),
    )
    coordinator.last_update_success = True
    entry.runtime_data = coordinator
    return coordinator, entry, transport


async def _entities_for(*snapshots: DecodedModelSnapshot):
    """Set up and return sensors for synthetic coordinator snapshots."""
    coordinator, entry, transport = _coordinator_with(*snapshots)
    entities = []
    await async_setup_entry(
        coordinator.hass,
        entry,
        lambda new_entities: entities.extend(new_entities),
    )
    return coordinator, entities, transport


async def _entities_for_devices(
    snapshots_by_device: dict[int, tuple[DecodedModelSnapshot, ...]],
):
    """Set up sensors for multiple independent Modbus device snapshots."""
    hass = FakeHass()
    entry = FakeEntry({})
    transports = {
        device_id: FakeTransport({}) for device_id in snapshots_by_device
    }
    coordinator = FroniusPVCoordinator(hass, entry, transports)
    coordinator.data = FroniusPVCoordinatorData(
        devices=tuple(
            DeviceSnapshot(
                device_id,
                tuple(snapshot.discovered for snapshot in snapshots),
                snapshots,
            )
            for device_id, snapshots in snapshots_by_device.items()
        )
    )
    coordinator.last_update_success = True
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        hass,
        entry,
        lambda new_entities: entities.extend(new_entities),
    )
    return coordinator, entities, transports


def _by_register(entities, name: str):
    """Return entities whose source uses the requested register name."""
    return [entity for entity in entities if entity._source.register_name == name]


def _string_words(value: str, size: int) -> tuple[int, ...]:
    """Encode an ASCII string into a fixed number of SunSpec words."""
    raw = value.encode("ascii").ljust(size * 2, b"\0")
    return tuple(
        int.from_bytes(raw[index : index + 2], "big")
        for index in range(0, len(raw), 2)
    )


def _model_1_payload(
    *,
    manufacturer: str = "Fronius",
    model: str = "Test inverter",
    version: str = "1.2.3",
    serial: str = "SERIAL123",
) -> list[int]:
    """Build representative decoded physical identity metadata."""
    payload = [0] * 65
    payload[0:16] = _string_words(manufacturer, 16)
    payload[16:32] = _string_words(model, 16)
    payload[40:48] = _string_words(version, 8)
    payload[48:64] = _string_words(serial, 16)
    payload[64] = 1
    return payload


@pytest.mark.asyncio
async def test_setup_creates_only_catalog_sensor_entities() -> None:
    """Scale factors and non-sensor catalog entries do not create sensors."""
    _, entities, _ = await _entities_for(_snapshot(MODEL_103), _snapshot(MODEL_124))

    expected = sum(
        register.entity is not None
        and register.entity.platform is EntityPlatform.SENSOR
        and register.entity.device_role is not None
        for definition in (MODEL_103, MODEL_124)
        for register in definition.registers
    )
    assert len(entities) == expected
    assert not _by_register(entities, "W_SF")
    assert not _by_register(entities, "ChaGriSet")


@pytest.mark.asyncio
async def test_unknown_discovered_model_creates_no_entities() -> None:
    """Unsupported discovery topology remains visible only to the coordinator."""
    coordinator, entry, _ = _coordinator_with()
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            DeviceSnapshot(1, (DiscoveredModel(999, 2, 45000),), ()),
        ),
    )
    entities = []

    await async_setup_entry(
        coordinator.hass,
        entry,
        lambda new_entities: entities.extend(new_entities),
    )

    assert entities == []


@pytest.mark.asyncio
async def test_physical_roles_create_separate_generic_devices_and_unique_ids() -> None:
    """Inverter, storage, and meter sensors belong to distinct devices."""
    _, entities, _ = await _entities_for(
        _snapshot(MODEL_103),
        _snapshot(MODEL_124),
        _snapshot(MODEL_203),
    )

    selected = [_by_register(entities, name)[0] for name in ("W", "ChaState")]
    selected.append(_by_register(entities, "W")[1])
    assert {entity._source.role for entity in selected} == {
        PhysicalDeviceRole.INVERTER,
        PhysicalDeviceRole.STORAGE,
        PhysicalDeviceRole.METER,
    }
    identifiers = {
        next(iter(entity.device_info["identifiers"])) for entity in selected
    }
    assert len(identifiers) == 3
    assert {entity.device_info["translation_key"] for entity in selected} == {
        "inverter_device",
        "storage_device",
        "meter_device",
    }
    assert len({entity.unique_id for entity in selected}) == 3
    assert {entity.entity_description.translation_key for entity in selected} == {
        "model_103_w",
        "model_124_chastate",
        "model_203_w",
    }
    assert {entity.unique_id for entity in selected} == {
        "test-entry_device1_inverter_model_103_w",
        "test-entry_device1_storage_model_124_chastate",
        "test-entry_device1_meter_model_203_w",
    }


@pytest.mark.asyncio
async def test_two_meter_device_ids_create_distinct_devices_and_entities() -> None:
    """Identical meter catalogs remain distinct through Modbus device identity."""
    _, entities, _ = await _entities_for_devices(
        {200: (_snapshot(MODEL_203),), 201: (_snapshot(MODEL_203),)}
    )
    meter_power = _by_register(entities, "W")

    assert len(meter_power) == 2
    assert {entity.unique_id for entity in meter_power} == {
        "test-entry_device200_meter_model_203_w",
        "test-entry_device201_meter_model_203_w",
    }
    assert {
        next(iter(entity.device_info["identifiers"])) for entity in meter_power
    } == {
        ("fronius_pv_manager", "test-entry:device200:meter"),
        ("fronius_pv_manager", "test-entry:device201:meter"),
    }
    assert {entity.device_info["translation_key"] for entity in meter_power} == {
        "meter_device_with_id",
    }
    assert {
        entity.device_info["translation_placeholders"]["device_id"]
        for entity in meter_power
    } == {"200", "201"}


@pytest.mark.asyncio
async def test_model_1_identity_populates_physical_device_info() -> None:
    """Already-decoded Common Model metadata presents the physical inverter."""
    _, entities, _ = await _entities_for(
        _snapshot(MODEL_1, _model_1_payload()),
        _snapshot(MODEL_103),
    )
    power = _by_register(entities, "W")[0]

    assert power.device_info["manufacturer"] == "Fronius"
    assert power.device_info["model"] == "Test inverter"
    assert power.device_info["name"] == "Test inverter"
    assert power.device_info["serial_number"] == "SERIAL123"
    assert power.device_info["sw_version"] == "1.2.3"
    assert power.device_info["identifiers"] == {
        ("fronius_pv_manager", "test-entry:device1:inverter")
    }


@pytest.mark.asyncio
async def test_blank_identity_uses_localized_fallback_without_fabrication() -> None:
    """Unknown product metadata remains unset instead of being guessed."""
    _, entities, _ = await _entities_for(
        _snapshot(MODEL_1),
        _snapshot(MODEL_103),
        _snapshot(MODEL_124),
    )
    inverter = _by_register(entities, "W")[0]
    storage = _by_register(entities, "ChaState")[0]

    assert inverter.device_info["translation_key"] == "inverter_device"
    assert "manufacturer" not in inverter.device_info
    assert storage.device_info["translation_key"] == "storage_device"
    assert "manufacturer" not in storage.device_info
    assert "model" not in storage.device_info


@pytest.mark.asyncio
async def test_partial_device_failure_only_marks_its_entities_unavailable() -> None:
    """Per-device status prevents one failed meter from hiding another meter."""
    coordinator, entities, _ = await _entities_for_devices(
        {200: (_snapshot(MODEL_203),), 201: (_snapshot(MODEL_203),)}
    )
    meter_power = _by_register(entities, "W")
    first_data, second_data = coordinator.data.devices
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            first_data,
            DeviceSnapshot(
                second_data.device_id,
                second_data.discovered_models,
                (),
                available=False,
            ),
        )
    )

    availability = {
        entity._source.device_id: entity.available for entity in meter_power
    }
    assert availability == {200: True, 201: False}


@pytest.mark.asyncio
async def test_inverter_only_snapshot_creates_no_storage_device() -> None:
    """Storage remains optional when no storage semantics are discovered."""
    _, entities, _ = await _entities_for_devices({1: (_snapshot(MODEL_103),)})

    assert entities
    assert all(
        entity._source.role is not PhysicalDeviceRole.STORAGE for entity in entities
    )


@pytest.mark.asyncio
async def test_entity_state_tracks_latest_snapshot_without_transport_reads() -> None:
    """An existing entity resolves its value from each new coordinator snapshot."""
    payload = [0] * 50
    payload[12] = 123
    coordinator, entities, transport = await _entities_for(
        _snapshot(MODEL_103, payload)
    )
    power = _by_register(entities, "W")[0]

    assert power.native_value == 123
    assert power.unique_id == "test-entry_device1_inverter_model_103_w"
    assert "model103-0" not in power.unique_id
    payload[12] = 456
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            DeviceSnapshot(
                1,
                coordinator.data.devices[0].discovered_models,
                (_snapshot(MODEL_103, payload),),
            ),
        ),
    )
    assert power.native_value == 456
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_fixed_unique_id_ignores_internal_model_occurrence() -> None:
    """Snapshot occurrence remains an internal lookup coordinate only."""
    coordinator, entities, _ = await _entities_for(_snapshot(MODEL_103))
    power = _by_register(entities, "W")[0]
    different_occurrence = replace(power._source, model_occurrence=7)

    recreated = FroniusPVSensor(
        coordinator,
        "test-entry",
        different_occurrence,
    )

    assert recreated.unique_id == power.unique_id
    assert recreated.unique_id == "test-entry_device1_inverter_model_103_w"
    assert "model103-7" not in recreated.unique_id


@pytest.mark.asyncio
async def test_invalid_value_and_coordinator_availability() -> None:
    """Invalid values are None while coordinator status controls availability."""
    payload = [0] * 50
    payload[12] = 0x8000
    coordinator, entities, _ = await _entities_for(_snapshot(MODEL_103, payload))
    power = _by_register(entities, "W")[0]

    assert power.native_value is None
    assert power.available
    coordinator.last_update_success = False
    assert not power.available
    coordinator.last_update_success = True
    assert power.available


@pytest.mark.asyncio
async def test_entity_defaults_categories_and_sensor_metadata() -> None:
    """Catalog policy and conservative unit metadata reach entity descriptions."""
    payload = [0] * 50
    payload[22:24] = [0, 10]
    _, entities, _ = await _entities_for(_snapshot(MODEL_103, payload))

    power = _by_register(entities, "W")[0]
    energy = _by_register(entities, "WH")[0]
    voltage = _by_register(entities, "PhVphA")[0]
    current = _by_register(entities, "AphA")[0]
    frequency = _by_register(entities, "Hz")[0]
    temperature = _by_register(entities, "TmpCab")[0]
    assert power.device_class is SensorDeviceClass.POWER
    assert power.entity_description.translation_key == "model_103_w"
    assert power.state_class is SensorStateClass.MEASUREMENT
    assert energy.device_class is SensorDeviceClass.ENERGY
    assert energy.state_class is SensorStateClass.TOTAL_INCREASING
    assert voltage.device_class is SensorDeviceClass.VOLTAGE
    assert current.device_class is SensorDeviceClass.CURRENT
    assert frequency.device_class is SensorDeviceClass.FREQUENCY
    assert temperature.device_class is SensorDeviceClass.TEMPERATURE
    assert temperature.entity_category is EntityCategory.DIAGNOSTIC
    assert not temperature.entity_description.entity_registry_enabled_default


@pytest.mark.asyncio
async def test_inverter_state_uses_translatable_enum_options() -> None:
    """Decoded SunSpec labels become stable HA enum option identifiers."""
    payload = [0] * 50
    payload[36] = 4
    _, entities, transport = await _entities_for(_snapshot(MODEL_103, payload))
    state = _by_register(entities, "St")[0]

    assert state.entity_description.translation_key == "model_103_st"
    assert state.device_class is SensorDeviceClass.ENUM
    assert state.options == [
        "off",
        "sleeping",
        "starting",
        "mppt",
        "throttled",
        "shutting_down",
        "fault",
        "standby",
    ]
    assert state.native_value == "mppt"
    assert state.unique_id == "test-entry_device1_inverter_model_103_st"
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_unknown_inverter_state_is_safe() -> None:
    """An unmapped future numeric state produces unknown instead of an error."""
    payload = [0] * 50
    payload[36] = 9
    _, entities, _ = await _entities_for(_snapshot(MODEL_103, payload))

    assert _by_register(entities, "St")[0].native_value is None


@pytest.mark.asyncio
async def test_storage_charge_status_uses_stable_translatable_options() -> None:
    """Every mapped storage status is exposed as a language-neutral option."""
    expected = [
        "off", "empty", "discharging", "charging", "full", "holding", "testing"
    ]
    for raw_value, option in enumerate(expected, start=1):
        payload = [0] * 24
        payload[9] = raw_value
        _, entities, transport = await _entities_for(_snapshot(MODEL_124, payload))
        status = _by_register(entities, "ChaSt")[0]

        assert status.device_class is SensorDeviceClass.ENUM
        assert status.options == expected
        assert status.native_value == option
        assert status.unique_id == "test-entry_device1_storage_model_124_chast"
        assert transport.read_calls == []


@pytest.mark.asyncio
async def test_unknown_storage_charge_status_is_safe() -> None:
    """An unmapped future storage status remains unknown without raising."""
    payload = [0] * 24
    payload[9] = 8
    _, entities, _ = await _entities_for(_snapshot(MODEL_124, payload))

    assert _by_register(entities, "ChaSt")[0].native_value is None


@pytest.mark.asyncio
async def test_other_mapped_sensor_enums_use_stable_options() -> None:
    """Mapped technical sensor enums also use Home Assistant enum semantics."""
    payload_120 = [0] * 26
    payload_120[0] = 82
    payload_103 = [0] * 50
    payload_103[37] = 1
    payload_121 = [0] * 30
    payload_121[15] = 1
    payload_121[16] = 2
    payload_121[19] = 3
    payload_123 = [0] * 24
    payload_123[19] = 2
    _, entities, _ = await _entities_for(
        _snapshot(MODEL_103, payload_103),
        _snapshot(MODEL_120, payload_120),
        _snapshot(MODEL_121, payload_121),
        _snapshot(MODEL_123, payload_123),
    )

    expected = {
        (103, "StVnd"): "off",
        (120, "DERTyp"): "pv_stor",
        (121, "VArAct"): "switch",
        (121, "ClcTotVA"): "arithmetic",
        (121, "ConnPh"): "c",
        (123, "VArPct_Mod"): "var_limit_as_a_percent_of_varmax",
    }
    for (model_id, register_name), option in expected.items():
        entity = next(
            item
            for item in entities
            if item._source.model_id == model_id
            and item._source.register_name == register_name
        )
        assert entity.device_class is SensorDeviceClass.ENUM
        assert entity.native_value == option


@pytest.mark.asyncio
async def test_storage_state_of_charge_uses_battery_percentage_metadata() -> None:
    """State of charge presents its decoded value as a battery percentage."""
    payload = [0] * 24
    payload[6] = 524
    payload[20] = 0xFFFF
    _, entities, transport = await _entities_for(_snapshot(MODEL_124, payload))
    state_of_charge = _by_register(entities, "ChaState")[0]

    assert state_of_charge.native_value == 52.4
    assert state_of_charge.native_unit_of_measurement == "%"
    assert state_of_charge.device_class is SensorDeviceClass.BATTERY
    assert state_of_charge.state_class is SensorStateClass.MEASUREMENT
    assert state_of_charge.unique_id == "test-entry_device1_storage_model_124_chastate"
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_power_factor_units_use_home_assistant_presentation() -> None:
    """Percentage and cosine power factors retain values with clean metadata."""
    payload_103 = [0] * 50
    payload_103[20] = 991
    payload_103[21] = 0xFFFF
    payload_120 = [0] * 26
    payload_120[12] = 0xFFF9
    payload_120[16] = 0xFFFF
    _, entities, transport = await _entities_for(
        _snapshot(MODEL_103, payload_103),
        _snapshot(MODEL_120, payload_120),
        _snapshot(MODEL_121),
        _snapshot(MODEL_203),
    )

    percentage = next(
        entity
        for entity in entities
        if entity._source.model_id == 103 and entity._source.register_name == "PF"
    )
    cosine = next(
        entity
        for entity in entities
        if entity._source.model_id == 120
        and entity._source.register_name == "PFRtgQ1"
    )
    assert percentage.native_value == 99.1
    assert percentage.native_unit_of_measurement == "%"
    assert percentage.device_class is SensorDeviceClass.POWER_FACTOR
    assert cosine.native_value == -0.7
    assert cosine.native_unit_of_measurement is None
    assert cosine.device_class is SensorDeviceClass.POWER_FACTOR
    audited = [
        entity
        for entity in entities
        if entity._source.register_name
        in {"PF", "PFphA", "PFphB", "PFphC", "PFRtgQ1", "PFRtgQ2",
            "PFRtgQ3", "PFRtgQ4", "PFMinQ1", "PFMinQ2", "PFMinQ3",
            "PFMinQ4"}
    ]
    assert audited
    assert all(
        entity.device_class is SensorDeviceClass.POWER_FACTOR for entity in audited
    )
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_status_resistance_and_epoch_counter_presentation() -> None:
    """Model 122 retains raw values while cleaning status names and units."""
    payload = [0] * 44
    payload[0] = 7
    payload[1] = 7
    payload[39:41] = [0x3229, 0x9E6F]
    payload[42] = 24623
    payload[43] = 2
    _, entities, transport = await _entities_for(_snapshot(MODEL_122, payload))
    pv_status = _by_register(entities, "PVConn")[0]
    storage_status = _by_register(entities, "StorConn")[0]
    timestamp_counter = _by_register(entities, "Tms")[0]
    resistance = _by_register(entities, "Ris")[0]

    assert pv_status.native_value == "Connected, Available, Operating"
    assert storage_status.native_value == "Connected, Available, Operating"
    assert pv_status.device_class is None
    assert pv_status.unique_id == "test-entry_device1_inverter_model_122_pvconn"
    assert timestamp_counter.native_value == 841588335
    assert timestamp_counter.native_unit_of_measurement == "s"
    assert timestamp_counter.device_class is None
    assert resistance.native_value == 2462300
    assert resistance.native_unit_of_measurement == "Ω"
    assert resistance.device_class is None
    assert resistance.state_class is SensorStateClass.MEASUREMENT
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_storage_ramp_rate_preserves_reference_unit() -> None:
    """Storage ramp rates retain their percentage-of-WChaMax semantics."""
    payload = [0] * 24
    payload[1] = 100
    payload[2] = 100
    _, entities, transport = await _entities_for(_snapshot(MODEL_124, payload))

    for register_name in ("WChaGra", "WDisChaGra"):
        rate = _by_register(entities, register_name)[0]
        assert rate.native_value == 100
        assert rate.native_unit_of_measurement == "% WChaMax/s"
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_model_160_epoch_counter_remains_numeric_seconds() -> None:
    """Module epoch-related counters are not misrepresented as HA timestamps."""
    payload = [0] * 88
    payload[6] = 1
    payload[8] = 1
    payload[9:17] = _string_words("MPPT 1", 8)
    payload[22:24] = [0x3229, 0x9E6F]
    _, entities, transport = await _entities_for(_snapshot(MODEL_160, payload))
    timestamp_counter = _by_register(entities, "Tms")[0]

    assert timestamp_counter.native_value == 841588335
    assert timestamp_counter.native_unit_of_measurement == "s"
    assert timestamp_counter.device_class is None
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_supported_seconds_duration_uses_duration_metadata() -> None:
    """A genuine ramp-time sensor uses seconds and the HA duration class."""
    payload = [0] * 24
    payload[6] = 12
    _, entities, transport = await _entities_for(_snapshot(MODEL_123, payload))
    ramp_time = _by_register(entities, "WMaxLimPct_RmpTms")[0]

    assert ramp_time.native_value == 12
    assert ramp_time.native_unit_of_measurement == "s"
    assert ramp_time.device_class is SensorDeviceClass.DURATION
    assert ramp_time.state_class is SensorStateClass.MEASUREMENT
    assert transport.read_calls == []


@pytest.mark.asyncio
async def test_model_160_modules_use_runtime_roles_and_stable_instance_ids() -> None:
    """Recognized repeated modules create sensors on their classified devices."""
    payload = [0] * 88
    payload[6] = 4
    for instance, name in enumerate(("MPPT 1", "StCha", "Unknown", "")):
        base = 8 + instance * 20
        payload[base] = instance + 1
        payload[base + 1 : base + 9] = _string_words(name, 8)
        payload[base + 9 : base + 14] = [10, 20, 30, 0, instance + 1]
    _, entities, _ = await _entities_for(_snapshot(MODEL_160, payload))

    powers = _by_register(entities, "DCW")
    assert len(powers) == 2
    assert {entity._source.role for entity in powers} == {
        PhysicalDeviceRole.INVERTER,
        PhysicalDeviceRole.STORAGE,
    }
    assert all(entity._source.entity.device_role is None for entity in powers)
    assert {entity._source.instance_index for entity in powers} == {0, 1}
    assert len({entity.unique_id for entity in powers}) == 2
    assert {entity.unique_id for entity in powers} == {
        "test-entry_device1_inverter_model_160_module_dcw_module_instance0",
        "test-entry_device1_storage_model_160_module_dcw_module_instance1",
    }
    assert all("model160-" not in entity.unique_id for entity in powers)
    assert {entity.entity_description.translation_key for entity in powers} == {
        "model_160_mppt_dcw",
        "model_160_storage_charging_dcw",
    }
    mppt = next(
        entity
        for entity in powers
        if entity._source.role is PhysicalDeviceRole.INVERTER
    )
    assert mppt.entity_description.translation_placeholders == {"number": "1"}
    register_names = {entity._source.register_name for entity in entities}
    assert all(name in register_names for name in ("DCA", "DCV", "DCW", "DCWH"))


@pytest.mark.asyncio
async def test_model_160_semantics_name_mppt_and_storage_flows_distinctly() -> None:
    """Classifier semantics select numbered MPPT and directional storage names."""
    payload = [0] * 88
    payload[6] = 4
    names = ("MPPT west", "StCha", "MPPT east", "StDisCha")
    for instance, name in enumerate(names):
        base = 8 + instance * 20
        payload[base] = instance + 1
        payload[base + 1 : base + 9] = _string_words(name, 8)
    _, entities, _ = await _entities_for(_snapshot(MODEL_160, payload))
    powers = _by_register(entities, "DCW")
    energy = _by_register(entities, "DCWH")

    mppt_powers = [
        entity
        for entity in powers
        if entity.entity_description.translation_key == "model_160_mppt_dcw"
    ]
    assert [
        entity.entity_description.translation_placeholders for entity in mppt_powers
    ] == [{"number": "1"}, {"number": "2"}]
    assert {entity.entity_description.translation_key for entity in powers} == {
        "model_160_mppt_dcw",
        "model_160_storage_charging_dcw",
        "model_160_storage_discharging_dcw",
    }
    assert {entity.entity_description.translation_key for entity in energy} == {
        "model_160_mppt_dcwh",
        "model_160_storage_charging_dcwh",
        "model_160_storage_discharging_dcwh",
    }
    assert {entity.unique_id for entity in powers} == {
        "test-entry_device1_inverter_model_160_module_dcw_module_instance0",
        "test-entry_device1_storage_model_160_module_dcw_module_instance1",
        "test-entry_device1_inverter_model_160_module_dcw_module_instance2",
        "test-entry_device1_storage_model_160_module_dcw_module_instance3",
    }


def test_home_assistant_translation_structures_cover_catalog_sensors() -> None:
    """Canonical and localized files contain the same complete sensor keys."""
    root = Path(__file__).parents[1] / "custom_components" / "fronius_pv_manager"
    documents = [
        json.loads((root / path).read_text(encoding="utf-8"))
        for path in ("strings.json", "translations/en.json", "translations/de.json")
    ]

    def structure(value):
        return (
            {key: structure(item) for key, item in value.items()}
            if isinstance(value, dict)
            else None
        )

    assert structure(documents[0]) == structure(documents[1])
    assert structure(documents[1]) == structure(documents[2])
    key_sets = [set(document["entity"]["sensor"]) for document in documents]
    assert key_sets[0] == key_sets[1] == key_sets[2]
    expected = set()
    for model in MODEL_DEFINITIONS_BY_ID.values():
        registers = list(model.registers)
        registers.extend(
            register
            for block in model.repeating_blocks
            for register in block.registers
        )
        expected.update(
            register.entity.translation_key
            for register in registers
            if register.entity is not None
            and register.entity.platform is EntityPlatform.SENSOR
        )
    assert expected <= key_sets[0]
    assert {
        "model_160_mppt_dcw",
        "model_160_storage_charging_dcw",
        "model_160_storage_discharging_dcw",
    } <= key_sets[0]
    assert documents[0]["device"].keys() == documents[1]["device"].keys()
    assert documents[1]["device"].keys() == documents[2]["device"].keys()


def test_storage_names_and_inverter_state_translations() -> None:
    """Storage compounds and operating states are natural in both languages."""
    root = Path(__file__).parents[1] / "custom_components" / "fronius_pv_manager"
    english = json.loads(
        (root / "translations/en.json").read_text(encoding="utf-8")
    )["entity"]["sensor"]
    german_text = (root / "translations/de.json").read_text(encoding="utf-8")
    assert "�" not in german_text
    german_document = json.loads(german_text)
    german = german_document["entity"]["sensor"]
    assert english["model_160_storage_charging_dcw"]["name"] == (
        "Storage charging power"
    )
    assert english["model_160_storage_discharging_dcwh"]["name"] == (
        "Storage discharging lifetime energy"
    )
    expected_german = {
        "model_124_chast": "Speicher-Ladestatus",
        "model_160_storage_charging_dca": "Speicher-Ladestrom",
        "model_160_storage_charging_dcv": "Speicher-Ladespannung",
        "model_160_storage_charging_dcw": "Speicher-Ladeleistung",
        "model_160_storage_charging_dcwh": "Speicher-Ladeenergie gesamt",
        "model_160_storage_discharging_dca": "Speicher-Entladestrom",
        "model_160_storage_discharging_dcv": "Speicher-Entladespannung",
        "model_160_storage_discharging_dcw": "Speicher-Entladeleistung",
        "model_160_storage_discharging_dcwh": "Speicher-Entladeenergie gesamt",
    }
    assert {
        key: german[key]["name"] for key in expected_german
    } == expected_german
    expected_options = {
        "off",
        "sleeping",
        "starting",
        "mppt",
        "throttled",
        "shutting_down",
        "fault",
        "standby",
    }
    assert set(english["model_103_st"]["state"]) == expected_options
    assert set(german["model_103_st"]["state"]) == expected_options
    assert german["model_103_st"]["state"] == {
        "off": "Aus",
        "sleeping": "Schlafmodus",
        "starting": "Startet",
        "mppt": "MPPT",
        "throttled": "Leistungsbegrenzt",
        "shutting_down": "Fährt herunter",
        "fault": "Fehler",
        "standby": "Bereitschaft",
    }
    translated_enum_keys = {
        "model_103_stvnd",
        "model_120_dertyp",
        "model_121_varact",
        "model_121_clctotva",
        "model_121_connph",
        "model_123_varpct_mod",
        "model_124_chast",
    }
    assert all(english[key].get("state") for key in translated_enum_keys)
    assert all(german[key].get("state") for key in translated_enum_keys)
    assert german["model_124_chast"]["state"] == {
        "off": "Aus",
        "empty": "Leer",
        "discharging": "Entlädt",
        "charging": "Lädt",
        "full": "Voll",
        "holding": "Holding",
        "testing": "Testbetrieb",
    }
    assert english["model_122_pvconn"]["name"] == "PV inverter status"
    assert english["model_122_storconn"]["name"] == "Storage inverter status"
    assert german["model_122_pvconn"]["name"] == "PV-Wechselrichterstatus"
    assert german["model_122_storconn"]["name"] == "Speicherwechselrichterstatus"
    assert english["model_124_wchagra"]["name"] == "Maximum charging ramp rate"
    assert english["model_124_wdischagra"]["name"] == (
        "Maximum discharging ramp rate"
    )
    assert german["model_124_wchagra"]["name"] == "Maximale Laderampe"
    assert german["model_124_wdischagra"]["name"] == "Maximale Entladerampe"
    assert german_document["config"]["step"]["user"]["data"]["device_ids"] == (
        "Modbus-Geräte-IDs"
    )
    assert german["model_103_tmpcab"]["name"] == "Gehäusetemperatur"
    assert german["model_103_tmpsnk"]["name"] == "Kühlkörpertemperatur"
    assert german["model_120_ahrrtg"]["name"] == "Nutzbare Batteriekapazität"
    assert german["model_120_artg"]["name"] == "Maximaler AC-RMS-Strom"
    assert german["model_120_dertyp"]["name"] == (
        "Typ der dezentralen Energieerzeugungsanlage"
    )
    assert german["model_121_clctotva"]["name"] == (
        "Berechnung der Gesamtscheinleistung"
    )
