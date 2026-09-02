"""Tests for Fronius PV Manager config-entry lifecycle."""

import logging

import pytest
from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.const import Platform

import custom_components.fronius_pv_manager as integration_module
import custom_components.fronius_pv_manager.number as number_module
import custom_components.fronius_pv_manager.select as select_module
import custom_components.fronius_pv_manager.sensor as sensor_module
from custom_components.fronius_pv_manager import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.fronius_pv_manager.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_HOST,
    CONF_PORT,
)
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from tests.runtime_fakes import (
    FakeEndpoint,
    FakeEntry,
    FakeHass,
    FakeTransport,
    model_chain,
)


def entry_data() -> dict[str, object]:
    """Return representative future config-flow connection data."""
    return {CONF_HOST: "192.0.2.30", CONF_PORT: 1502, CONF_DEVICE_ID: 7}


def install_endpoint_factory(monkeypatch, transports):
    """Install and return one observable shared endpoint factory result."""
    endpoint = FakeEndpoint(transports)
    calls = []

    def factory(host, *, port):
        calls.append((host, port))
        return endpoint

    monkeypatch.setattr(integration_module, "ModbusTcpEndpointTransport", factory)
    return endpoint, calls


@pytest.mark.asyncio
async def test_successful_setup_stores_initialized_runtime_data(monkeypatch) -> None:
    """Setup discovers, refreshes, and stores the coordinator on the entry."""
    registers, _ = model_chain((1, 65), (999, 2))
    transport = FakeTransport(registers)
    endpoint, factory_calls = install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass()
    entry = FakeEntry(entry_data())

    assert await async_setup_entry(hass, entry)

    assert factory_calls == [("192.0.2.30", 1502)]
    assert isinstance(entry.runtime_data, FroniusPVCoordinator)
    assert entry.runtime_data.last_update_success
    assert [model.model_id for model in entry.runtime_data.data.discovered_models] == [
        1,
        999,
    ]
    assert endpoint.connect_calls == 1
    assert hass.config_entries.forwarded == [
        (entry, (Platform.SENSOR, Platform.NUMBER, Platform.SELECT))
    ]
    assert len(entry.runtime_data.write_policies) == 25
    assert {
        coordinate
        for coordinate, policy in entry.runtime_data.write_policies.items()
        if policy.enabled
    } == {(124, "MinRsvPct"), (124, "ChaGriSet")}


@pytest.mark.asyncio
async def test_invalid_existing_policy_disables_writes_but_setup_continues(
    monkeypatch, tmp_path, caplog
) -> None:
    """Invalid operator YAML fails closed without disabling read-only polling."""
    policy_path = tmp_path / "fronius_pv_manager" / "write_policy.yaml"
    policy_path.parent.mkdir()
    invalid_content = (
        "version: 1\nmodels:\n  124:\n    ChaGriSet:\n      values: [2]\n"
    )
    policy_path.write_text(invalid_content, encoding="utf-8")
    registers, _ = model_chain((124, 24))
    transport = FakeTransport(registers)
    install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass(config_dir=tmp_path)
    entry = FakeEntry(entry_data())

    with caplog.at_level(logging.ERROR):
        assert await async_setup_entry(hass, entry)

    assert not entry.runtime_data.write_policies
    assert entry.runtime_data.last_update_success
    assert policy_path.read_text(encoding="utf-8") == invalid_content
    assert "writes are disabled" in caplog.text
    assert str(policy_path) in caplog.text
    numbers = []
    selects = []
    sensors = []
    await number_module.async_setup_entry(
        hass, entry, lambda items: numbers.extend(items)
    )
    await select_module.async_setup_entry(
        hass, entry, lambda items: selects.extend(items)
    )
    await sensor_module.async_setup_entry(
        hass, entry, lambda items: sensors.extend(items)
    )
    assert any(entity._source.register_name == "OutWRte" for entity in numbers)
    assert {entity._source.register_name for entity in selects} == {
        "StorCtl_Mod",
        "ChaGriSet",
    }
    assert sensors


@pytest.mark.asyncio
async def test_temporary_connection_failure_raises_entry_not_ready(
    monkeypatch,
) -> None:
    """A temporarily unavailable endpoint fails setup and closes safely."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers, connection_error=True)
    endpoint, _ = install_endpoint_factory(monkeypatch, {7: transport})

    with pytest.raises(ConfigEntryNotReady) as raised:
        await async_setup_entry(FakeHass(), FakeEntry(entry_data()))

    assert raised.value.__cause__ is not None
    assert endpoint.close_calls == 1


@pytest.mark.asyncio
async def test_unload_stops_coordinator_closes_transport_and_clears_runtime(
    monkeypatch,
) -> None:
    """Unload shuts down polling and releases the persistent Modbus client."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers)
    endpoint, _ = install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass()
    entry = FakeEntry(entry_data())
    await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data

    assert await async_unload_entry(hass, entry)

    assert coordinator._shutdown_requested
    assert endpoint.close_calls == 1
    assert not hasattr(entry, "runtime_data")
    assert hass.config_entries.unloaded == [
        (entry, (Platform.SENSOR, Platform.NUMBER, Platform.SELECT))
    ]


@pytest.mark.asyncio
async def test_close_failure_does_not_break_unload(monkeypatch) -> None:
    """A broken client close is logged without leaving the entry loaded."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers, close_error=True)
    endpoint, _ = install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass()
    entry = FakeEntry(entry_data())
    await async_setup_entry(hass, entry)

    assert await async_unload_entry(hass, entry)
    assert endpoint.close_calls == 1
    assert not hasattr(entry, "runtime_data")


@pytest.mark.asyncio
async def test_platform_forwarding_failure_rolls_back_runtime(monkeypatch) -> None:
    """A platform setup failure shuts down and closes the new runtime."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers)
    endpoint, _ = install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass()
    hass.config_entries.forward_error = RuntimeError("platform setup failed")
    entry = FakeEntry(entry_data())

    with pytest.raises(RuntimeError, match="platform setup failed"):
        await async_setup_entry(hass, entry)

    assert endpoint.close_calls == 1
    assert not hasattr(entry, "runtime_data")


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_runtime_active(monkeypatch) -> None:
    """A refused platform unload leaves polling and transport untouched."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers)
    endpoint, _ = install_endpoint_factory(monkeypatch, {7: transport})
    hass = FakeHass()
    entry = FakeEntry(entry_data())
    await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data
    hass.config_entries.unload_result = False

    assert not await async_unload_entry(hass, entry)

    assert entry.runtime_data is coordinator
    assert not coordinator._shutdown_requested
    assert endpoint.close_calls == 0


@pytest.mark.asyncio
async def test_multiple_configured_device_ids_share_one_endpoint(
    monkeypatch,
) -> None:
    """One endpoint creates two device-bound views for both participants."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    transports = {
        1: FakeTransport(registers_1),
        200: FakeTransport(registers_200),
    }
    endpoint, factory_calls = install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry(
        {
            CONF_HOST: "192.0.2.30",
            CONF_PORT: 1502,
            CONF_DEVICE_IDS: (1, 200),
        }
    )

    assert await async_setup_entry(hass, entry)

    assert factory_calls == [("192.0.2.30", 1502)]
    assert endpoint.connect_calls == 1
    assert len(entry.runtime_data.transports) == 2
    assert {
        transport.endpoint for transport in entry.runtime_data.transports.values()
    } == {endpoint}
    assert [device.device_id for device in entry.runtime_data.data.devices] == [
        1,
        200,
    ]


@pytest.mark.asyncio
async def test_multi_device_platform_failure_closes_all_transports(
    monkeypatch,
) -> None:
    """Forwarding rollback releases every device context owned by the entry."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    transports = {
        1: FakeTransport(registers_1),
        200: FakeTransport(registers_200),
    }
    endpoint, _ = install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    hass.config_entries.forward_error = RuntimeError("platform setup failed")
    entry = FakeEntry({CONF_HOST: "192.0.2.30", CONF_DEVICE_IDS: (1, 200)})

    with pytest.raises(RuntimeError, match="platform setup failed"):
        await async_setup_entry(hass, entry)

    assert endpoint.close_calls == 1


@pytest.mark.asyncio
async def test_multi_device_discovery_failure_closes_all_transports(
    monkeypatch,
) -> None:
    """Discovery rollback closes contexts created before and after the failure."""
    registers, _ = model_chain((103, 50))
    transports = {
        1: FakeTransport(registers),
        200: FakeTransport(registers, connection_error=True),
    }
    endpoint, _ = install_endpoint_factory(monkeypatch, transports)
    entry = FakeEntry({CONF_HOST: "192.0.2.30", CONF_DEVICE_IDS: (1, 200)})

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(FakeHass(), entry)

    assert endpoint.close_calls == 1


@pytest.mark.asyncio
async def test_multi_device_unload_closes_all_or_preserves_all(monkeypatch) -> None:
    """Platform unload outcome consistently owns every device transport."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    transports = {
        1: FakeTransport(registers_1),
        200: FakeTransport(registers_200),
    }
    endpoint, _ = install_endpoint_factory(monkeypatch, transports)
    hass = FakeHass()
    entry = FakeEntry({CONF_HOST: "192.0.2.30", CONF_DEVICE_IDS: (1, 200)})
    await async_setup_entry(hass, entry)
    hass.config_entries.unload_result = False

    assert not await async_unload_entry(hass, entry)
    assert endpoint.close_calls == 0

    hass.config_entries.unload_result = True
    assert await async_unload_entry(hass, entry)
    assert endpoint.close_calls == 1
