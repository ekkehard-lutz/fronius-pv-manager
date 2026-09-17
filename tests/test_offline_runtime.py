"""Validated topology survives offline starts and repeated independent outages."""

import json

import pytest

from custom_components.fronius_pv_manager import (
    async_setup_entry,
    async_unload_entry,
    number,
    select,
    sensor,
    switch,
)
from custom_components.fronius_pv_manager.config_flow import _validate_endpoint
from custom_components.fronius_pv_manager.const import CONF_DEVICE_IDS, CONF_HOST
from custom_components.fronius_pv_manager.solar_entity import SolarEntity
from custom_components.fronius_pv_manager.storage_status import StorageControlStatus
from custom_components.fronius_pv_manager.topology import CONF_TOPOLOGY
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain
from tests.test_init import install_endpoint_factory


def configured_devices():
    """Include storage and repeating modules on the inverter's own unit."""
    registers, bases = model_chain((1, 65), (103, 50), (124, 24), (160, 48))
    registers[bases[124]] = 6000
    for index, name in enumerate(("MPPT1", "StCha")):
        raw = name.encode().ljust(16, b"\0")
        for offset in range(8):
            registers[bases[160] + 9 + index * 20 + offset] = int.from_bytes(
                raw[offset * 2 : offset * 2 + 2], "big"
            )
    meter, _ = model_chain((203, 105))
    return {7: FakeTransport(registers), 42: FakeTransport(meter)}, bases


async def load_platforms(hass, entry):
    """Create real entity classes and retain automatic additions."""
    entities = []
    for platform in (sensor, number, select, switch):
        await platform.async_setup_entry(
            hass,
            entry,
            lambda items: entities.extend(
                e for e in items if not isinstance(e, SolarEntity)
            ),
        )
    return entities


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [(7,), (42,), (7, 42)])
async def test_validated_offline_start_and_repeated_recovery(monkeypatch, offline):
    """All platforms exist offline, recover, and isolate subsequent outages."""
    transports, _ = configured_devices()
    validation = _validate_endpoint(
        "192.0.2.1",
        502,
        (7, 42),
        lambda host, *, port, device_id: transports[device_id],
    )
    # Exercise the same JSON round-trip as persisted config-entry data.
    topology = json.loads(json.dumps(validation.topology))
    assert topology["7"][1]["fixed"] == {}
    assert topology["7"][3]["repeating"]["module"][0]["values"] == {
        "ID": 0,
        "IDStr": "MPPT1",
    }
    for unit in offline:
        transports[unit].connection_error = True
    install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry(
        {CONF_HOST: "192.0.2.1", CONF_DEVICE_IDS: [7, 42], CONF_TOPOLOGY: topology}
    )
    assert await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data
    entities = await load_platforms(hass, entry)
    assert entities
    assert {type(entity) for entity in entities} == {
        sensor.FroniusPVSensor,
        StorageControlStatus,
        number.FroniusPVNumber,
        select.FroniusPVSelect,
        number.StorageNumber,
        select.StorageMode,
        switch.GridChargingSwitch,
    }
    assert {entity._source.device_id for entity in entities} == {7, 42}
    assert any(entity._source.block_name == "module" for entity in entities)
    identities = [entity.unique_id for entity in entities]
    for entity in entities:
        assert entity.available == (entity._source.device_id not in offline)
        if entity._source.device_id in offline:
            value = (
                entity.current_option
                if isinstance(entity, (select.FroniusPVSelect, select.StorageMode))
                else entity.is_on
                if isinstance(entity, switch.GridChargingSwitch)
                else entity.native_value
            )
            if isinstance(entity, StorageControlStatus):
                assert value == "unknown"
            else:
                assert value is None
    for transport in transports.values():
        transport.connection_error = False
    await coordinator.async_refresh()
    assert all(entity.available for entity in entities)
    for unit in (42, 7, 42):
        transports[unit].fail_reads = True
        await coordinator.async_refresh()
        assert all(
            entity.available == (entity._source.device_id != unit)
            for entity in entities
        )
        transports[unit].fail_reads = False
        await coordinator.async_refresh()
        assert all(entity.available for entity in entities)
    assert [entity.unique_id for entity in entities] == identities
    assert len(hass.config_entries.forwarded) == 1
    assert entry.runtime_data is coordinator
    assert await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_legacy_offline_entry_discovers_and_adds_entities_without_reload(
    monkeypatch,
):
    """Entries predating the cache learn missing structure on a normal update."""
    transports, _ = configured_devices()
    for transport in transports.values():
        transport.connection_error = True
    install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry({CONF_HOST: "192.0.2.1", CONF_DEVICE_IDS: [7, 42]})
    assert await async_setup_entry(hass, entry)
    entities = await load_platforms(hass, entry)
    assert not entities
    coordinator = entry.runtime_data
    assert coordinator._unsub_refresh is not None
    for unit in (42, 7):
        transports[unit].connection_error = False
        await coordinator.async_refresh()
        assert any(entity._source.device_id == unit for entity in entities)
    assert set(entry.data[CONF_TOPOLOGY]) == {"7", "42"}
    identities = [entity.unique_id for entity in entities]
    await coordinator.async_refresh()
    assert [entity.unique_id for entity in entities] == identities
    assert len(hass.config_entries.forwarded) == 1
    assert await async_unload_entry(hass, entry)
    # The newly learned cache now constructs exactly the same entities offline.
    for transport in transports.values():
        transport.connection_error = True
    assert await async_setup_entry(hass, entry)
    restored = await load_platforms(hass, entry)
    assert {entity.unique_id for entity in restored} == set(identities)
    assert all(not entity.available for entity in restored)
    assert await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_model_failure_preserves_other_models_and_repeating_identity(monkeypatch):
    """Storage model failure leaves inverter and meter measurements available."""
    transports, bases = configured_devices()
    install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry({CONF_HOST: "192.0.2.1", CONF_DEVICE_IDS: [7, 42]})
    assert await async_setup_entry(hass, entry)
    entities = await load_platforms(hass, entry)
    read = transports[7].read_holding_registers

    def fail_storage(address, count):
        if address == bases[124]:
            raise ModbusTransportError("storage model timed out")
        return read(address, count)

    transports[7].read_holding_registers = fail_storage
    await entry.runtime_data.async_refresh()
    assert all(
        entity.available == (entity._source.model_id != 124) for entity in entities
    )
    transports[7].read_holding_registers = read
    await entry.runtime_data.async_refresh()
    assert all(entity.available for entity in entities)
    assert await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_cached_platforms_are_constructed_before_any_runtime_io(monkeypatch):
    """Loading platforms is independent of the first device communication."""
    transports, _ = configured_devices()
    validation = _validate_endpoint(
        "192.0.2.1",
        502,
        (7, 42),
        lambda host, *, port, device_id: transports[device_id],
    )
    for transport in transports.values():
        transport.read_calls.clear()
    endpoint, _ = install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry(
        {
            CONF_HOST: "192.0.2.1",
            CONF_DEVICE_IDS: [7, 42],
            CONF_TOPOLOGY: validation.topology,
        }
    )
    entities = []

    async def forward(entry, platforms):
        assert endpoint.connect_calls == 0
        assert all(not transport.read_calls for transport in transports.values())
        entities.extend(await load_platforms(hass, entry))
        assert entities
        assert all(not entity.available for entity in entities)

    hass.config_entries.async_forward_entry_setups = forward
    assert await async_setup_entry(hass, entry)
    assert all(entity.available for entity in entities)
    assert await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_nine_semantic_sensors_persisted_topology_lifecycle(monkeypatch):
    """Persist identity only, then recover both conversion directions in place."""
    from custom_components.fronius_pv_manager.register_maps import (
        MODEL_103,
        MODEL_120,
        MODEL_124,
        MODEL_203,
    )

    registers, bases = model_chain((103, 50), (120, 26), (124, 24), (160, 68))
    meter_registers, meter_bases = model_chain((203, 105))
    transports = {7: FakeTransport(registers), 42: FakeTransport(meter_registers)}

    def fixed(unit, definition, **values):
        base = (bases if unit == 7 else meter_bases)[definition.model_ids[0]]
        for name, value in values.items():
            register = next(r for r in definition.registers if r.name == name)
            for word in range(register.size):
                transports[unit].registers[base + register.offset + word] = (
                    value >> (16 * (register.size - word - 1))
                ) & 0xFFFF

    dc_base = bases[160]
    transports[7].registers[dc_base + 6] = 3
    for index, name in enumerate(("MPPT 1", "StCha", "StDisCha")):
        base = dc_base + 8 + 20 * index
        transports[7].registers[base] = index + 1
        encoded = name.encode().ljust(16, b"\0")
        for word in range(8):
            transports[7].registers[base + 1 + word] = int.from_bytes(
                encoded[2 * word : 2 * word + 2], "big"
            )
    # Model 160 counters and power use zero scale factors.
    transports[7].registers[dc_base + 8 + 11] = 1000
    transports[7].registers[dc_base + 28 + 13] = 10000
    transports[7].registers[dc_base + 48 + 13] = 8000
    fixed(7, MODEL_103, W=900, St=4)
    fixed(7, MODEL_120, WHRtg=1000)
    fixed(7, MODEL_124, WChaMax=6000, ChaState=50)
    fixed(42, MODEL_203, W=-400)

    validation = _validate_endpoint(
        "192.0.2.1",
        502,
        (7, 42),
        lambda host, *, port, device_id: transports[device_id],
    )
    topology = json.loads(json.dumps(validation.topology))
    for transport in transports.values():
        transport.connection_error = True
    install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry(
        {
            CONF_HOST: "192.0.2.1",
            CONF_DEVICE_IDS: [7, 42],
            CONF_TOPOLOGY: topology,
        }
    )
    assert await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data
    entities = []
    await sensor.async_setup_entry(hass, entry, entities.extend)
    keys = {
        "pv_power",
        "grid_import_power",
        "grid_export_power",
        "consumption_power",
        "autarky",
        "self_consumption",
        "inverter_efficiency",
        "rectifier_efficiency",
        "battery_lifetime_efficiency",
    }

    def semantic():
        return {
            e.key: e for e in entities if isinstance(e, SolarEntity) and e.key in keys
        }

    original = semantic()
    assert set(original) == keys
    ids = {key: e.unique_id for key, e in original.items()}
    assert all(not e.available and e.native_value is None for e in original.values())
    try:
        for transport in transports.values():
            transport.connection_error = False
        await coordinator.async_refresh()
        expected = {
            "pv_power": 1000,
            "grid_import_power": 0,
            "grid_export_power": 400,
            "consumption_power": 500,
            "autarky": 100,
            "self_consumption": 100 * 500 / 900,
            "inverter_efficiency": 90,
            "rectifier_efficiency": None,
            "battery_lifetime_efficiency": 85,
        }

        def check(values):
            current = semantic()
            assert (
                sum(isinstance(e, SolarEntity) and e.key in keys for e in entities) == 9
            )
            assert all(current[key] is original[key] for key in keys)
            assert {key: e.unique_id for key, e in current.items()} == ids
            for key, value in values.items():
                assert current[key].available == (value is not None)
                assert current[key].native_value == (
                    pytest.approx(value) if value is not None else None
                )

        check(expected)
        for unit in (42, 7):
            transports[unit].fail_reads = True
            await coordinator.async_refresh()
            affected = (
                {
                    "grid_import_power",
                    "grid_export_power",
                    "consumption_power",
                    "autarky",
                    "self_consumption",
                }
                if unit == 42
                else keys - {"grid_import_power", "grid_export_power"}
            )
            check(
                {
                    key: None if key in affected else value
                    for key, value in expected.items()
                }
            )
            transports[unit].fail_reads = False
            await coordinator.async_refresh()
            check(expected)
        # A new outage must not use values from the topology cache.
        for transport in transports.values():
            transport.fail_reads = True
        await coordinator.async_refresh()
        check(dict.fromkeys(keys))
        for transport in transports.values():
            transport.fail_reads = False
        # Reverse conversion makes the other efficiency sensor recover in place.
        fixed(7, MODEL_103, W=-500)
        fixed(42, MODEL_203, W=600)
        transports[7].registers[dc_base + 8 + 11] = 100
        transports[7].registers[dc_base + 28 + 11] = 500
        await coordinator.async_refresh()
        check(
            {
                "pv_power": 100,
                "grid_import_power": 600,
                "grid_export_power": 0,
                "consumption_power": 100,
                "autarky": 0,
                "self_consumption": 100,
                "inverter_efficiency": None,
                "rectifier_efficiency": 80,
                "battery_lifetime_efficiency": 85,
            }
        )
    finally:
        assert await async_unload_entry(hass, entry)
