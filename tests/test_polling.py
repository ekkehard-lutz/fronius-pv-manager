"""Deterministic selective-polling, publication, recovery and cost regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant

import custom_components.fronius_pv_manager.coordinator as coordinator_module
from custom_components.fronius_pv_manager.const import (
    CONF_FAST_SCAN_INTERVAL,
    CONF_SLOW_SCAN_INTERVAL,
)
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    SolarPowerSensor,
    _sensor_sources,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain


@pytest.fixture
def clock(monkeypatch):
    """Control only coordinator time, never asyncio/global monotonic time."""
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    return clock


def runtime(*models, options=None):
    registers, bases = model_chain(*models)
    # Model 160's documented four-module payload must advertise four modules.
    if 160 in bases:
        registers[bases[160] + 6] = 4
    entry = FakeEntry({})
    entry.options = options or {}
    transport = FakeTransport(registers)
    return FroniusPVCoordinator(FakeHass(), entry, {1: transport}), transport, bases


@pytest.mark.asyncio
async def test_defaults_and_selective_publication(clock):
    coordinator, transport, bases = runtime((1, 65), (103, 50))
    assert coordinator.fast_interval == 5
    assert coordinator.slow_interval == 30
    assert coordinator.update_interval.total_seconds() == 5
    await coordinator.async_refresh()
    slow = coordinator.data.decoded_models[0]
    published = []
    # Do not install a timer: observe the actual coordinator publication boundary.
    coordinator.async_update_listeners = lambda: published.append(coordinator.data)
    for now in (5, 10, 15, 20, 25, 30):
        clock.now = now
        transport.read_calls.clear()
        transport.registers[bases[103] + 12] = now
        await coordinator.async_refresh()
        assert transport.read_calls.count((bases[103], 50)) == 1
        assert ((bases[1], 65) in transport.read_calls) == (now == 30)
        assert published[-1] is coordinator.data
        assert coordinator.data.decoded_models[1].decoded.fixed["W"].value == now
        if now < 30:
            assert coordinator.data.decoded_models[0] is slow
        else:
            assert coordinator.data.decoded_models[0] is not slow


@pytest.mark.asyncio
async def test_non_multiple_deadlines_do_not_accumulate_rounding_drift(clock):
    coordinator, transport, bases = runtime(
        (1, 65),
        (103, 50),
        options={CONF_FAST_SCAN_INTERVAL: 7, CONF_SLOW_SCAN_INTERVAL: 30},
    )
    observed = []
    for now in range(0, 127, 7):
        clock.now = now
        transport.read_calls.clear()
        await coordinator.async_refresh()
        assert transport.read_calls.count((bases[103], 50)) == 1
        if (bases[1], 65) in transport.read_calls:
            observed.append(now)
    assert observed == [0, 35, 63, 91, 126]
    assert coordinator._slow_deadlines[1] == 150


@pytest.mark.asyncio
async def test_delayed_poll_skips_missed_periods_without_burst(clock):
    coordinator, transport, bases = runtime((1, 65), (103, 50))
    await coordinator.async_refresh()
    clock.now = 107
    transport.read_calls.clear()
    await coordinator.async_refresh()
    assert transport.read_calls == [(bases[1], 65), (bases[103], 50)]
    assert coordinator._slow_deadlines[1] == 120
    clock.now = 110
    transport.read_calls.clear()
    await coordinator.async_refresh()
    assert transport.read_calls == [(bases[103], 50)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_model", [1, 103, 124])
async def test_failed_fast_slow_or_battery_model_recovers(clock, failed_model):
    coordinator, transport, bases = runtime((1, 65), (103, 50), (124, 24))
    await coordinator.async_refresh()
    read = transport.read_holding_registers

    def fail(address, count):
        if address == bases[failed_model]:
            raise ModbusTransportError("selected model offline")
        return read(address, count)

    transport.read_holding_registers = fail
    clock.now = 30 if failed_model == 1 else 5
    await coordinator.async_refresh()
    snapshots = {m.discovered.model_id: m for m in coordinator.data.decoded_models}
    assert not snapshots[failed_model].available
    assert snapshots[failed_model].decoded.fixed == {}
    assert all(m.available for key, m in snapshots.items() if key != failed_model)
    assert not coordinator.live_models(1)
    transport.read_holding_registers = read
    clock.now += 5
    transport.read_calls.clear()
    await coordinator.async_refresh()
    assert all(m.available for m in coordinator.data.decoded_models)
    assert (bases[1], 65) in transport.read_calls  # recovery includes SLOW


@pytest.mark.asyncio
async def test_discovery_refresh_is_full_and_does_not_duplicate_fast(clock):
    coordinator, transport, bases = runtime((1, 65), (103, 50))
    await coordinator.async_refresh()
    clock.now = 301
    transport.read_calls.clear()
    await coordinator.async_refresh()
    assert transport.read_calls.count((bases[103], 50)) == 1
    assert transport.read_calls.count((bases[1], 65)) == 1
    assert (40000, 2) in transport.read_calls
    assert coordinator._slow_deadlines[1] == 330


@pytest.mark.asyncio
async def test_explicit_post_write_refresh_reads_slow_settings(clock):
    coordinator, transport, bases = runtime((103, 50), (123, 24))
    await coordinator.async_refresh()
    clock.now = 5
    transport.read_calls.clear()
    transport.registers[bases[123] + 3] = 75
    coordinator._debounced_refresh.async_call = AsyncMock(
        side_effect=coordinator.async_refresh
    )
    await coordinator.async_request_refresh()
    assert (bases[123], 24) in transport.read_calls
    assert transport.read_calls.count((bases[103], 50)) == 1
    assert coordinator._slow_deadlines[1] == 30
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_unchanged_fast_reports_but_cached_slow_does_not(clock, tmp_path):
    coordinator, transport, _ = runtime((120, 26), (103, 50))
    await coordinator.async_refresh()
    hass = HomeAssistant(str(tmp_path))
    sensors = []
    for model_id in (120, 103):
        source = next(s for s in _sensor_sources(coordinator) if s.model_id == model_id)
        entity = FroniusPVSensor(coordinator, "test", source)
        entity_id = f"sensor.model_{model_id}"
        entity.async_write_ha_state = lambda e=entity, eid=entity_id: (
            hass.states.async_set(eid, str(e.native_value), {"unit": "test"})
        )
        entity._handle_coordinator_update()
        sensors.append(entity)
    original = {
        mid: (
            hass.states.get(f"sensor.model_{mid}").last_changed,
            hass.states.get(f"sensor.model_{mid}").last_updated,
            hass.states.get(f"sensor.model_{mid}").last_reported,
        )
        for mid in (120, 103)
    }
    clock.now = 5
    await coordinator.async_refresh()
    for entity in sensors:
        entity._handle_coordinator_update()
    for mid in (120, 103):
        state = hass.states.get(f"sensor.model_{mid}")
        assert state.last_changed == original[mid][0]
        assert state.last_updated == original[mid][1]
        assert (state.last_reported > original[mid][2]) == (mid == 103)
    # Failed reads retain sensor values without publishing a fresh report.
    transport.fail_reads = True
    clock.now = 30
    await coordinator.async_refresh()
    for entity in sensors:
        entity.async_write_ha_state = Mock()
        entity._handle_coordinator_update()
        entity.async_write_ha_state.assert_not_called()
        assert entity.available


@pytest.mark.asyncio
async def test_semantic_grid_power_changes_on_fast_refresh(clock):
    coordinator, transport, bases = runtime((1, 65), (203, 105))
    await coordinator.async_refresh()
    sensor = SolarPowerSensor(coordinator, "test", 1, "grid_import_power", "meter")
    assert sensor.value == 0
    clock.now = 5
    transport.registers[bases[203] + 16] = 1234
    await coordinator.async_refresh()
    assert sensor.value == 1234


@pytest.mark.asyncio
async def test_documented_topology_request_accounting(clock):
    coordinator, inverter, _ = runtime(
        (1, 65),
        (103, 50),
        (120, 26),
        (121, 30),
        (122, 44),
        (123, 24),
        (124, 24),
        (160, 88),
    )
    registers, _ = model_chain((1, 65), (203, 105))
    meter = FakeTransport(registers)
    coordinator = FroniusPVCoordinator(
        FakeHass(), FakeEntry({}), {1: inverter, 200: meter}
    )
    await coordinator.async_discover()
    reads = inverter.read_calls + meter.read_calls
    assert (len(reads), sum(count for _, count in reads)) == (14, 28)
    for now, expected in [(0, (11, 521)), (5, (5, 267)), (30, (11, 521))]:
        clock.now = now
        inverter.read_calls.clear()
        meter.read_calls.clear()
        await coordinator.async_refresh()
        reads = inverter.read_calls + meter.read_calls
        assert (len(reads), sum(count for _, count in reads)) == expected
        assert all(m.available for m in coordinator.data.decoded_models)


@pytest.mark.asyncio
async def test_cycle_diagnostics_label_planned_counts(clock, caplog):
    coordinator, _, _ = runtime((1, 65), (103, 50))
    await coordinator.async_refresh()
    clock.now = 5
    with caplog.at_level("DEBUG", logger=coordinator_module.__name__):
        await coordinator.async_refresh()
    assert "Poll FAST unit=1 models=" in caplog.text
    assert "planned_reads=1 planned_registers=50 discovery=False" in caplog.text
    assert "success=True" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_unit", [1, 200])
async def test_cached_identity_cannot_hide_offline_dynamic_device(clock, failed_unit):
    registers, _ = model_chain((1, 65), (103, 50))
    inverter = FakeTransport(registers)
    registers, _ = model_chain((1, 65), (203, 105))
    meter = FakeTransport(registers)
    transports = {1: inverter, 200: meter}
    coordinator = FroniusPVCoordinator(FakeHass(), FakeEntry({}), transports)
    await coordinator.async_refresh()
    clock.now = 5
    transports[failed_unit].fail_reads = True
    await coordinator.async_refresh()
    for device in coordinator.data.devices:
        assert device.available == (device.device_id != failed_unit)
        assert device.decoded_models[0].available  # identity retained, not remeasured
    transports[failed_unit].fail_reads = False
    clock.now = 10
    await coordinator.async_refresh()
    assert all(device.available for device in coordinator.data.devices)


@pytest.mark.asyncio
async def test_cached_slow_replaced_after_discovery_changes_coordinates(clock):
    coordinator, transport, _ = runtime((120, 26), (103, 50))
    await coordinator.async_refresh()
    old = coordinator.data.decoded_models[0]
    # Change ordering and model addresses on rediscovery, keeping model IDs.
    registers, bases = model_chain((103, 50), (120, 26))
    registers[bases[120]] = 1
    transport.registers = registers
    clock.now = 300
    await coordinator.async_refresh()
    new = coordinator.data.decoded_models[1]
    assert new.discovered != old.discovered
    assert new.decoded != old.decoded
    assert new.available
