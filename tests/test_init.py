"""Tests for Fronius PV Manager config-entry lifecycle."""

import pytest
from homeassistant.config_entries import ConfigEntryNotReady

import custom_components.fronius_pv_manager as integration_module
from custom_components.fronius_pv_manager import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.fronius_pv_manager.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
)
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain


def entry_data() -> dict[str, object]:
    """Return representative future config-flow connection data."""
    return {CONF_HOST: "192.0.2.30", CONF_PORT: 1502, CONF_DEVICE_ID: 7}


@pytest.mark.asyncio
async def test_successful_setup_stores_initialized_runtime_data(monkeypatch) -> None:
    """Setup discovers, refreshes, and stores the coordinator on the entry."""
    registers, _ = model_chain((1, 65), (999, 2))
    transport = FakeTransport(registers)
    factory_calls = []

    def factory(host, *, port, device_id):
        factory_calls.append((host, port, device_id))
        return transport

    monkeypatch.setattr(integration_module, "ModbusTcpTransport", factory)
    hass = FakeHass()
    entry = FakeEntry(entry_data())

    assert await async_setup_entry(hass, entry)

    assert factory_calls == [("192.0.2.30", 1502, 7)]
    assert isinstance(entry.runtime_data, FroniusPVCoordinator)
    assert entry.runtime_data.last_update_success
    assert [model.model_id for model in entry.runtime_data.data.discovered_models] == [
        1,
        999,
    ]
    assert transport.connect_calls == 1


@pytest.mark.asyncio
async def test_temporary_connection_failure_raises_entry_not_ready(
    monkeypatch,
) -> None:
    """A temporarily unavailable endpoint fails setup and closes safely."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers, connection_error=True)
    monkeypatch.setattr(
        integration_module,
        "ModbusTcpTransport",
        lambda *args, **kwargs: transport,
    )

    with pytest.raises(ConfigEntryNotReady) as raised:
        await async_setup_entry(FakeHass(), FakeEntry(entry_data()))

    assert raised.value.__cause__ is not None
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_unload_stops_coordinator_closes_transport_and_clears_runtime(
    monkeypatch,
) -> None:
    """Unload shuts down polling and releases the persistent Modbus client."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers)
    monkeypatch.setattr(
        integration_module,
        "ModbusTcpTransport",
        lambda *args, **kwargs: transport,
    )
    hass = FakeHass()
    entry = FakeEntry(entry_data())
    await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data

    assert await async_unload_entry(hass, entry)

    assert coordinator._shutdown_requested
    assert transport.close_calls == 1
    assert not hasattr(entry, "runtime_data")


@pytest.mark.asyncio
async def test_close_failure_does_not_break_unload(monkeypatch) -> None:
    """A broken client close is logged without leaving the entry loaded."""
    registers, _ = model_chain((1, 65))
    transport = FakeTransport(registers, close_error=True)
    monkeypatch.setattr(
        integration_module,
        "ModbusTcpTransport",
        lambda *args, **kwargs: transport,
    )
    hass = FakeHass()
    entry = FakeEntry(entry_data())
    await async_setup_entry(hass, entry)

    assert await async_unload_entry(hass, entry)
    assert transport.close_calls == 1
    assert not hasattr(entry, "runtime_data")
