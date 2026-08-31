"""Tests for catalog-backed Home Assistant sensor entities."""

from collections.abc import Iterable
from dataclasses import replace

import pytest
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import EntityCategory

from custom_components.fronius_pv_manager.coordinator import (
    DecodedModelSnapshot,
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
    MODEL_103,
    MODEL_124,
    MODEL_160,
    MODEL_203,
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
    coordinator = FroniusPVCoordinator(hass, entry, transport)
    coordinator.data = FroniusPVCoordinatorData(
        discovered_models=tuple(snapshot.discovered for snapshot in snapshots),
        decoded_models=snapshots,
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
        discovered_models=(DiscoveredModel(999, 2, 45000),),
        decoded_models=(),
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
    assert {entity.device_info["name"] for entity in selected} == {
        "Fronius Inverter",
        "Fronius Storage",
        "Fronius Smart Meter",
    }
    assert len({entity.unique_id for entity in selected}) == 3
    assert {entity.unique_id for entity in selected} == {
        "test-entry_inverter_model_103_w",
        "test-entry_storage_model_124_chastate",
        "test-entry_meter_model_203_w",
    }


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
    assert power.unique_id == "test-entry_inverter_model_103_w"
    assert "model103-0" not in power.unique_id
    payload[12] = 456
    coordinator.data = FroniusPVCoordinatorData(
        discovered_models=coordinator.data.discovered_models,
        decoded_models=(_snapshot(MODEL_103, payload),),
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
    assert recreated.unique_id == "test-entry_inverter_model_103_w"
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
        "test-entry_inverter_model_160_module_dcw_module_instance0",
        "test-entry_storage_model_160_module_dcw_module_instance1",
    }
    assert all("model160-" not in entity.unique_id for entity in powers)
    register_names = {entity._source.register_name for entity in entities}
    assert all(name in register_names for name in ("DCA", "DCV", "DCW", "DCWH"))
