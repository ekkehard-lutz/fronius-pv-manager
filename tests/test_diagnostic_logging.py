"""Four-level diagnostics with deterministic Modbus I/O and HA reporting."""

import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

import custom_components.fronius_pv_manager as integration
from custom_components.fronius_pv_manager import diagnostic_logging as module
from custom_components.fronius_pv_manager import transport as transport_module
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.diagnostic_logging import (
    MAX_EPISODE_SIGNATURES,
    Diagnostics,
    diagnostic_level,
)
from custom_components.fronius_pv_manager.sensor import SolarEfficiencySensor
from custom_components.fronius_pv_manager.transport import (
    ModbusConnectionError,
    ModbusTcpEndpointTransport,
)
from tests.runtime_fakes import FakeEntry, FakeHass, model_chain
from tests.test_efficiency_sensors import sources
from tests.test_measurement_publication import publish_sources, raw_sensor
from tests.test_polling import clock as clock_fixture
from tests.test_polling import runtime
from tests.test_polling_options import options_flow
from tests.test_sensor import _coordinator_with
from tests.test_storage_control import controller
from tests.test_storage_remote import remote as remote_fixture

remote = remote_fixture

clock = clock_fixture


def records(caplog, event=None):
    result = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("FPMGR "):
            fields = json.loads(message.split(" ", 2)[2])
            fields["severity"] = record.levelname
            fields["subsystem"] = message.split(" ", 2)[1].split("=")[1]
            if event is None or fields["event"] == event:
                result.append(fields)
    return result


class Client:
    """One mock TCP client; no real network, retry or write operations."""

    def __init__(self, units):
        self.units = units
        self.connected = False
        self.connect_result = True
        self.connect_calls = 0
        self.close_calls = 0
        self.read_calls = []
        self.errors = {}

    def connect(self):
        self.connect_calls += 1
        self.connected = self.connect_result
        return self.connected

    def close(self):
        self.close_calls += 1
        self.connected = False

    def read_holding_registers(self, address, *, count, device_id):
        self.read_calls.append((device_id, address, count))
        error = self.errors.get((device_id, address), self.errors.get(device_id))
        if isinstance(error, Exception):
            raise error
        if error is not None:
            return error
        return SimpleNamespace(
            isError=lambda: False,
            registers=[self.units[device_id][address + i] for i in range(count)],
        )


def endpoint_runtime(monkeypatch, clock, *, models=((103, 50),), units=(1,), level=1):
    registers, bases = model_chain(*models)
    if 160 in bases:
        registers[bases[160] + 6] = 4
    client = Client({unit: dict(registers) for unit in units})
    monkeypatch.setattr(transport_module, "ModbusTcpClient", lambda *a, **kw: client)
    endpoint = ModbusTcpEndpointTransport("192.0.2.1")
    entry = FakeEntry({})
    entry.options = {"diagnostic_level": level}
    coordinator = FroniusPVCoordinator(
        FakeHass(), entry, {unit: endpoint.bind(unit) for unit in units}
    )
    coordinator.diagnostics.clock = lambda: clock.now
    return coordinator, endpoint, client, bases


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 2, 3])
@pytest.mark.parametrize("units", [(1,), (1, 200)])
async def test_initial_connection_during_entry_setup(
    level, units, monkeypatch, clock, caplog
):
    initial, endpoint, client, _ = endpoint_runtime(
        monkeypatch, clock, units=units, level=level
    )
    entry, hass = initial.entry, initial.hass
    entry.data = {"host": "192.0.2.1", "device_ids": list(units)}
    monkeypatch.setattr(
        integration, "ModbusTcpEndpointTransport", lambda *a, **kw: endpoint
    )
    assert await integration.async_setup_entry(hass, entry)
    try:
        assert entry.runtime_data.last_update_success
        for _ in range(3):
            endpoint.connect()
        assert client.connect_calls == 1
        assert endpoint.generation == 1
        events = records(caplog, "connection_established")
        assert events == (
            []
            if level == 0
            else [
                {
                    "event": "connection_established",
                    "entry_id": entry.entry_id,
                    "connection_generation": 1,
                    "reason": "initial",
                    "result": "connected",
                    "severity": "WARNING",
                    "subsystem": "modbus",
                }
            ]
        )
        assert not records(caplog, "endpoint_recovered")
        assert not records(caplog, "communication_recovered")
    finally:
        assert await integration.async_unload_entry(hass, entry)


@pytest.mark.parametrize("reconnect", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_failed_connects_do_not_report_success(
    reconnect, raises, monkeypatch, clock, caplog
):
    _, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    expected_generation = 0
    if reconnect:
        endpoint.connect()
        endpoint.close()
        expected_generation = 2
        caplog.clear()
    successful_connect = client.connect
    failed_connect = (
        Mock(side_effect=TimeoutError()) if raises else Mock(return_value=False)
    )
    monkeypatch.setattr(client, "connect", failed_connect)
    for now in (5, 10, 15):
        clock.now = now
        with pytest.raises(ModbusConnectionError):
            endpoint.connect()
        expected_generation += 1
        assert endpoint.generation == expected_generation
        assert not endpoint._connected
        assert not records(caplog, "connection_established")
    monkeypatch.setattr(client, "connect", successful_connect)
    clock.now = 20
    endpoint.connect()
    endpoint.connect()
    events = records(caplog, "connection_established")
    assert len(events) == 1
    assert events[0]["reason"] == ("reconnect" if reconnect else "initial")
    assert events[0]["connection_generation"] == expected_generation + 1
    assert events[0]["result"] == "connected"


@pytest.mark.asyncio
async def test_each_reconnection_within_300_seconds_preserves_recovery(
    monkeypatch, clock, caplog
):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    for now in (5, 15):
        client.connected = False
        clock.now = now
        await coordinator.async_refresh()
        assert coordinator.write_authority(1) is None
        clock.now = now + 5
        await coordinator.async_refresh()
        assert coordinator.write_authority(1) is not None
    events = records(caplog, "connection_established")
    assert [r["reason"] for r in events] == ["initial", "reconnect", "reconnect"]
    assert [r["connection_generation"] for r in events] == [1, 4, 7]
    assert all(r["entry_id"] == coordinator.entry.entry_id for r in events)
    assert client.connect_calls == 3
    assert endpoint.generation == 7
    for event in (
        "connection_lost",
        "session_reset",
        "discovery_recovered",
        "communication_recovered",
        "endpoint_recovered",
    ):
        assert len(records(caplog, event)) == 2


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_reconnect_without_active_failure_episode(level, monkeypatch, clock, caplog):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock, level=level)
    endpoint.connect()
    for now in (5, 10):
        clock.now = now
        endpoint.close()
        assert not coordinator.diagnostics.active(endpoint.diagnostic_scope)
        endpoint.connect()
    events = records(caplog, "connection_established")
    if level == 0:
        assert not events
        assert client.connect_calls == 3
        return
    assert [r["reason"] for r in events] == ["initial", "reconnect", "reconnect"]
    assert [r["connection_generation"] for r in events] == [1, 3, 5]
    assert client.connect_calls == 3


@pytest.mark.asyncio
async def test_live_enable_does_not_replay_connection(monkeypatch, clock, caplog):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock, level=0)
    await coordinator.async_refresh()
    entry, hass = coordinator.entry, coordinator.hass
    entry.runtime_data = coordinator
    hass.config_entries.async_reload = AsyncMock()
    entry.options = {"diagnostic_level": 1}
    await integration._async_options_updated(hass, entry)
    clock.now = 5
    await coordinator.async_refresh()
    endpoint.connect()
    assert not records(caplog, "connection_established")
    assert client.connect_calls == 1
    client.connected = False
    clock.now = 10
    await coordinator.async_refresh()
    clock.now = 15
    await coordinator.async_refresh()
    events = records(caplog, "connection_established")
    assert len(events) == 1
    assert events[0]["reason"] == "reconnect"
    assert events[0]["connection_generation"] == 4
    hass.config_entries.async_reload.assert_not_called()


@pytest.mark.parametrize("level", [0, 1, 2, 3])
@pytest.mark.asyncio
async def test_valid_level_persistence_and_defaults(level):
    flow, entry = options_flow()
    form = await flow.async_step_init()
    assert form["data_schema"]({})["diagnostic_level"] == 0
    result = await flow.async_step_init({"diagnostic_level": level})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "diagnostic_level": level,
        "communication_availability_timeout": 300,
        "fast_scan_interval": 5,
        "slow_scan_interval": 30,
    }
    entry.options = result["data"]
    assert (await flow.async_step_init())["data_schema"]({})[
        "diagnostic_level"
    ] == level


@pytest.mark.parametrize("invalid", [-1, 4, True, False, 1.5, "2", None])
@pytest.mark.asyncio
async def test_invalid_levels_rejected(invalid):
    flow, _ = options_flow()
    result = await flow.async_step_init({"diagnostic_level": invalid})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"diagnostic_level": "invalid_diagnostic_level"}
    assert diagnostic_level({"diagnostic_level": invalid}) == 0


def test_german_english_translations():
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        data = json.loads(
            (Path("custom_components/fronius_pv_manager") / name).read_text()
        )
        step = data["options"]["step"]["init"]
        assert step["data"]["diagnostic_level"]
        assert all(
            f"{level}:" in step["data_description"]["diagnostic_level"]
            for level in range(4)
        )
        assert data["options"]["error"]["invalid_diagnostic_level"]


@pytest.mark.asyncio
async def test_level_only_change_keeps_live_runtime_and_schedule(monkeypatch, clock):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    coordinator.async_add_listener(lambda: None)
    timer = coordinator._unsub_refresh
    hass, entry = coordinator.hass, coordinator.entry
    entry.runtime_data = coordinator
    hass.config_entries.async_reload = AsyncMock()
    data = coordinator.measurement_data
    deadlines = dict(coordinator._slow_deadlines)
    generation = endpoint.generation
    calls = list(client.read_calls)
    control = coordinator.storage_control
    lease = object()
    control._leases[1] = lease
    for level in (0, 3, 2, 1):
        entry.options = {"diagnostic_level": level}
        await integration._async_options_updated(hass, entry)
        assert coordinator.diagnostics.enabled(level)
        assert entry.runtime_data is coordinator
        assert coordinator.measurement_data is data
        assert coordinator._slow_deadlines == deadlines
        assert endpoint.generation == generation
        assert client.read_calls == calls and client.close_calls == 0
        assert coordinator._unsub_refresh is timer
        assert control._leases[1] is lease
    hass.config_entries.async_reload.assert_not_called()
    control._leases.clear()
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_timeout_episode_dedup_recovery_and_new_episode(
    monkeypatch, clock, caplog
):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    caplog.clear()
    client.errors[1] = TimeoutError("secret configuration must not be logged")
    for now in (5, 10, 15, 20):
        clock.now = now
        await coordinator.async_refresh()
    assert len(records(caplog, "communication_failure")) == 1
    assert len(records(caplog, "session_reset")) == 1
    assert len(records(caplog, "rediscovery_started")) == 1
    assert len(records(caplog, "discovery_failed")) == 1
    assert all(r["severity"] == "WARNING" for r in records(caplog))
    assert "secret configuration" not in caplog.text
    assert records(caplog, "communication_failure")[0]["reason"] == "timeout"
    assert coordinator.write_authority(1) is None
    # Connection is genuine, but discovery has not recovered yet.
    connections = records(caplog, "connection_established")
    assert len(connections) == 3
    assert all(r["reason"] == "reconnect" for r in connections)
    assert not records(caplog, "discovery_recovered")
    client.errors.clear()
    clock.now = 25
    await coordinator.async_refresh()
    assert len(records(caplog, "communication_recovered")) == 1
    assert len(records(caplog, "endpoint_recovered")) == 1
    assert len(records(caplog, "discovery_recovered")) == 1
    assert coordinator.write_authority(1) is not None
    clock.now = 30
    await coordinator.async_refresh()
    assert len(records(caplog, "communication_recovered")) == 1
    client.errors[1] = TimeoutError()
    clock.now = 35
    await coordinator.async_refresh()
    assert len(records(caplog, "communication_failure")) == 2
    assert len(records(caplog, "session_reset")) == 2


@pytest.mark.asyncio
async def test_materially_different_error_is_visible(monkeypatch, clock, caplog):
    coordinator, _, client, bases = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    client.errors[(1, bases[103])] = TimeoutError()
    clock.now = 5
    await coordinator.async_refresh()
    client.errors.clear()
    client.errors[1] = SimpleNamespace(isError=lambda: True)
    clock.now = 10
    await coordinator.async_refresh()
    assert {r["reason"] for r in records(caplog, "communication_failure")} == {
        "timeout",
        "error_response",
    }


@pytest.mark.asyncio
async def test_single_warning_summaries_and_bounded_storage(clock, caplog):
    entry = {"diagnostic_level": 1}
    diag = Diagnostics(lambda: entry, clock=lambda: clock.now)
    for now in range(0, 301, 5):
        clock.now = now
        diag.failure(("device", 1), "modbus", "communication_failure", reason="timeout")
    warnings = records(caplog, "communication_failure")
    assert len(warnings) == 2 and warnings[1]["suppressed"] == 59
    for index in range(200):
        diag.failure(("device", 1), "modbus", "other", reason=f"class_{index}")
    assert len(diag._episodes[("device", 1)]) <= MAX_EPISODE_SIGNATURES


@pytest.mark.asyncio
async def test_failed_reconnect_never_reports_success(monkeypatch, clock, caplog):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    client.errors[1] = TimeoutError()
    clock.now = 5
    await coordinator.async_refresh()
    caplog.clear()
    client.connect_result = False
    for now in (10, 15, 20):
        clock.now = now
        await coordinator.async_refresh()
    assert len(records(caplog, "connection_failure")) == 1
    assert len(records(caplog, "connection_attempt")) == 1
    assert not records(caplog, "connection_established")
    assert not records(caplog, "communication_recovered")
    assert not endpoint._connected
    client.connect_result = True
    client.errors.clear()
    clock.now = 25
    await coordinator.async_refresh()
    assert records(caplog, "connection_established")
    assert records(caplog, "discovery_recovered")


@pytest.mark.asyncio
async def test_shared_endpoint_reset_warns_once_but_devices_are_distinct(
    monkeypatch, clock, caplog
):
    coordinator, endpoint, client, _ = endpoint_runtime(
        monkeypatch, clock, units=(1, 200)
    )
    await coordinator.async_refresh()
    client.errors = {1: TimeoutError(), 200: TimeoutError()}
    clock.now = 5
    await coordinator.async_refresh()
    assert len(records(caplog, "session_reset")) == 1
    assert {r["device_id"] for r in records(caplog, "communication_failure")} == {
        1,
        200,
    }
    client.errors.pop(200)
    clock.now = 10
    await coordinator.async_refresh()
    assert not records(caplog, "endpoint_recovered")
    assert coordinator.write_authority(1) is None
    assert coordinator.write_authority(200) is not None
    client.errors.clear()
    clock.now = 15
    await coordinator.async_refresh()
    assert len(records(caplog, "endpoint_recovered")) == 1


@pytest.mark.asyncio
async def test_connection_loss_and_only_actual_reset(monkeypatch, clock, caplog):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    client.connected = False
    clock.now = 5
    await coordinator.async_refresh()
    assert records(caplog, "connection_lost")
    assert records(caplog, "session_reset")
    assert client.close_calls > 0
    # Plain fake transports have no TCP reset; diagnostics must not invent one.
    caplog.clear()
    fake, transport, _ = runtime((103, 50))
    fake.entry.options["diagnostic_level"] = 1
    await fake.async_refresh()
    transport.fail_reads = True
    await fake.async_refresh()
    assert records(caplog, "communication_failure")
    assert not records(caplog, "session_reset")


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 2, 3])
@pytest.mark.parametrize("units", [(1,), (1, 200)])
async def test_healthy_polling_and_scheduled_renewal(
    level, units, monkeypatch, clock, caplog
):
    coordinator, _, client, _ = endpoint_runtime(
        monkeypatch, clock, units=units, level=level
    )
    for now in range(0, 306, 5):
        clock.now = now
        await coordinator.async_refresh()
    events = records(caplog)
    forbidden = {
        "communication_failure",
        "session_reset",
        "rediscovery_started",
        "discovery_failed",
        "connection_attempt",
        "communication_recovered",
    }
    assert not any(r["event"] in forbidden for r in events)
    connections = records(caplog, "connection_established")
    assert len(connections) == (0 if level == 0 else 1)
    if connections:
        assert connections[0]["reason"] == "initial"
    assert client.connect_calls == 1
    if level == 0:
        assert not events
    if level == 1:
        assert events == connections
    if level == 3:
        assert len(records(caplog, "poll_summary")) == 6 * len(units)
        assert len(records(caplog, "discovery_completed")) == 2 * len(units)
        assert all(r["severity"] == "WARNING" for r in events)
    # Per unit: 62 payload reads and two discovery sequences (3 requests each).
    assert len(client.read_calls) == 68 * len(units)


@pytest.mark.asyncio
async def test_live_transitions_ignore_retained_measurements(
    monkeypatch, clock, caplog
):
    coordinator, _, client, bases = endpoint_runtime(monkeypatch, clock, level=2)
    await coordinator.async_refresh()
    from custom_components.fronius_pv_manager.register_maps import MODEL_103

    offset = next(r.offset for r in MODEL_103.registers if r.name == "St")
    client.units[1][bases[103] + offset] = 4
    clock.now = 5
    await coordinator.async_refresh()
    assert len(records(caplog, "operating_state_changed")) == 1
    caplog.clear()
    for now in (10, 15):
        clock.now = now
        await coordinator.async_refresh()
    assert not records(caplog, "operating_state_changed")
    client.errors[1] = TimeoutError()
    clock.now = 20
    await coordinator.async_refresh()
    assert coordinator.measurement_data.devices[0].available
    assert records(caplog, "device_availability_changed")[-1]["result"] == "offline"
    assert records(caplog, "write_authority_changed")[-1]["result"] == "invalid"
    assert not records(caplog, "operating_state_changed")


@pytest.mark.asyncio
async def test_logging_failure_cannot_break_polling_or_control(monkeypatch, clock):
    coordinator, endpoint, client, _ = endpoint_runtime(monkeypatch, clock, level=3)
    monkeypatch.setattr(
        module._LOGGER, "log", Mock(side_effect=RuntimeError("logger broken"))
    )
    await coordinator.async_refresh()
    client.errors[1] = TimeoutError()
    clock.now = 5
    await coordinator.async_refresh()
    assert coordinator.measurement_data.devices[0].available
    client.errors.clear()
    clock.now = 10
    await coordinator.async_refresh()
    assert coordinator.write_authority(1) is not None
    control_coordinator, control = controller()
    control_coordinator.entry.options["diagnostic_level"] = 3
    await control.async_change(1, mode="manual")
    assert control_coordinator.control_transport.write_calls
    assert control.mode(1) == "manual"


@pytest.mark.asyncio
async def test_diagnostics_do_not_create_reports_or_median_samples(
    monkeypatch, clock, tmp_path
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=3)
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    hass = HomeAssistant(str(tmp_path))
    entity.async_write_ha_state = lambda: hass.states.async_set(
        "sensor.power", str(entity.native_value)
    )
    entity.async_write_ha_state()
    original = hass.states.get("sensor.power")
    timestamps = (original.last_changed, original.last_updated, original.last_reported)
    client.errors[1] = TimeoutError()
    for now in (5, 10, 15, 30):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
    state = hass.states.get("sensor.power")
    assert (state.last_changed, state.last_updated, state.last_reported) == timestamps
    assert entity.available
    # Actual formula samples with diagnostics enabled are still not refilled on failure.
    calc, _, _ = _coordinator_with(*sources())
    calc.entry.options["diagnostic_level"] = 3
    efficiency = SolarEfficiencySensor(
        calc, "test", 1, "inverter_efficiency", "inverter"
    )
    efficiency.async_write_ha_state = Mock()
    publish_sources(calc, efficiency, sources(), 0, slow=True)
    failed = tuple(replace(s, available=False, read_failed=True) for s in sources())
    for now in (5, 10, 30):
        publish_sources(calc, efficiency, failed, now, slow=now == 30)
    assert len(efficiency._window.samples) == 1
    assert efficiency.native_value == 96


@pytest.mark.asyncio
async def test_failed_second_chunk_is_held_and_identified(monkeypatch, clock, caplog):
    coordinator, _, client, bases = endpoint_runtime(
        monkeypatch, clock, models=((203, 105),), level=3
    )
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator, 203)
    client.errors[(1, bases[203] + 100)] = TimeoutError()
    clock.now = 5
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.assert_not_called()
    assert entity.available
    assert records(caplog, "model_read_failed")[0]["model_id"] == 203
    assert coordinator.write_authority(1) is None


@pytest.mark.parametrize(
    "response,reason",
    [
        (SimpleNamespace(isError=lambda: True), "error_response"),
        (SimpleNamespace(isError=lambda: False), "invalid_response"),
        (SimpleNamespace(isError=lambda: False, registers=[]), "incomplete_response"),
    ],
)
@pytest.mark.asyncio
async def test_bad_responses_are_level_one_warnings(
    response, reason, monkeypatch, clock, caplog
):
    coordinator, _, client, bases = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    client.errors[(1, bases[103])] = response
    clock.now = 5
    await coordinator.async_refresh()
    assert records(caplog, "communication_failure")[0]["reason"] == reason
    assert records(caplog, "communication_failure")[0]["severity"] == "WARNING"


@pytest.mark.asyncio
async def test_unexpected_error_always_visible_at_level_zero(
    monkeypatch, clock, caplog
):
    coordinator, _, _, _ = endpoint_runtime(monkeypatch, clock, level=0)
    monkeypatch.setattr(
        coordinator, "_poll_devices", Mock(side_effect=RuntimeError("defect"))
    )
    await coordinator.async_refresh()
    assert records(caplog, "runtime_error")[0]["severity"] == "ERROR"


def test_failed_json_formatting_is_isolated(caplog):
    diag = Diagnostics(lambda: {"diagnostic_level": 3})
    assert not diag.event(3, "control", "test", invalid=object())
    assert not records(caplog)


@pytest.mark.asyncio
async def test_level_change_while_poll_io_is_active(monkeypatch, clock):
    import asyncio
    import threading

    coordinator, endpoint, client, bases = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    started, release = threading.Event(), threading.Event()
    read = client.read_holding_registers

    def blocking_read(address, **kwargs):
        if address == bases[103]:
            started.set()
            assert release.wait(5)
        return read(address, **kwargs)

    async def executor(target, *args):
        return await asyncio.to_thread(target, *args)

    monkeypatch.setattr(client, "read_holding_registers", blocking_read)
    coordinator.hass.async_add_executor_job = executor
    entry = coordinator.entry
    entry.runtime_data = coordinator
    coordinator.hass.config_entries.async_reload = AsyncMock()
    clock.now = 5
    task = asyncio.create_task(coordinator.async_refresh())
    try:
        assert await asyncio.to_thread(started.wait, 3)
        generation = endpoint.generation
        entry.options = {"diagnostic_level": 3}
        await asyncio.wait_for(
            integration._async_options_updated(coordinator.hass, entry), 1
        )
        assert coordinator.io_lock.locked() and not task.done()
        assert endpoint.generation == generation and client.close_calls == 0
        coordinator.hass.config_entries.async_reload.assert_not_called()
    finally:
        release.set()
        await task
    assert coordinator.data.devices[0].available


@pytest.mark.asyncio
async def test_level_change_keeps_existing_median_history():
    calc, _, _ = _coordinator_with(*sources())
    calc.entry.runtime_data = calc
    calc.hass.config_entries.async_reload = AsyncMock()
    efficiency = SolarEfficiencySensor(
        calc, "test", 1, "inverter_efficiency", "inverter"
    )
    efficiency.async_write_ha_state = Mock()
    publish_sources(calc, efficiency, sources(), 0, slow=True)
    publish_sources(calc, efficiency, sources(ac=4700), 5)
    window = efficiency._window
    samples = tuple(window.samples)
    calc.entry.options = {"diagnostic_level": 3}
    await integration._async_options_updated(calc.hass, calc.entry)
    assert efficiency._window is window and tuple(window.samples) == samples
    assert efficiency.native_value == 96
    calc.hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_discovery_keeps_authority_and_sensor_invalid(
    monkeypatch, clock, caplog
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock)
    await coordinator.async_refresh()
    sensor = raw_sensor(coordinator)
    client.units[1][40000] = 0
    clock.now = 300
    await coordinator.async_refresh()
    sensor._handle_coordinator_update()
    assert not sensor.available
    assert coordinator.write_authority(1) is None
    assert records(caplog, "discovery_failed")[0]["reason"] == "invalid_sunspec"
    assert not records(caplog, "discovery_recovered")


@pytest.mark.asyncio
async def test_zero_level_never_warns_on_expected_timeout(monkeypatch, clock, caplog):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=0)
    await coordinator.async_refresh()
    client.errors[1] = TimeoutError()
    for now in (5, 10, 15):
        clock.now = now
        await coordinator.async_refresh()
    assert not records(caplog)
    coordinator.entry.options = {"diagnostic_level": 1}
    clock.now = 20
    await coordinator.async_refresh()
    assert records(caplog, "communication_failure")


@pytest.mark.asyncio
async def test_control_results_and_rejections_are_level_two(caplog):
    from homeassistant.exceptions import ServiceValidationError

    coor, control = controller()
    coor.entry.options["diagnostic_level"] = 2
    await control.async_change(1, mode="manual")
    assert records(caplog, "control_completed")
    count = len(coor.control_transport.write_calls)
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, mode="invalid")
    assert records(caplog, "control_failed")
    assert len(coor.control_transport.write_calls) == count


@pytest.mark.asyncio
async def test_protocol_decode_error_is_always_visible(monkeypatch, clock, caplog):
    coordinator, _, _, _ = endpoint_runtime(
        monkeypatch, clock, level=0, models=((103, 49),)
    )
    # A payload shorter than its required fixed fields is a structural defect.
    await coordinator.async_refresh()
    assert records(caplog, "protocol_error")[0]["severity"] == "ERROR"


@pytest.mark.asyncio
async def test_topology_and_capability_transitions_are_not_periodic(
    monkeypatch, clock, caplog
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=2)
    await coordinator.async_refresh()
    registers, _ = model_chain((103, 50), (124, 24))
    client.units[1] = registers
    clock.now = 300
    await coordinator.async_refresh()
    assert len(records(caplog, "topology_changed")) == 1
    assert records(caplog, "write_authority_changed")
    for now in (305, 310):
        clock.now = now
        await coordinator.async_refresh()
    assert len(records(caplog, "topology_changed")) == 1


@pytest.mark.asyncio
async def test_diagnostic_collection_errors_are_isolated(monkeypatch, clock):
    coor, _, client, _ = endpoint_runtime(monkeypatch, clock, level=3)
    coor.diagnostics.options = Mock(
        side_effect=RuntimeError("options getter unavailable")
    )
    await coor.async_refresh()
    assert coor.data.devices[0].available
    client.errors[1] = TimeoutError()
    clock.now = 5
    await coor.async_refresh()
    assert coor.measurement_data.devices[0].available


@pytest.mark.asyncio
async def test_identical_requests_at_all_levels_have_same_poll_traffic(
    monkeypatch, clock
):
    calls = []
    for level in range(4):
        clock.now = 0
        coordinator, _, client, _ = endpoint_runtime(
            monkeypatch, clock, level=level, models=((1, 65), (103, 50), (203, 105))
        )
        for now in range(0, 66, 5):
            clock.now = now
            await coordinator.async_refresh()
        calls.append(client.read_calls)
    assert calls[0] and all(trace == calls[0] for trace in calls)


@pytest.mark.asyncio
async def test_live_level_change_preserves_real_remote_lease(remote):
    from tests.test_storage_remote import OWNER, PROFILE

    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    lease = control._leases[1]
    watchdog = control._watchdogs[1]
    writes = list(coordinator.control_transport.write_calls)
    coordinator.entry.runtime_data = coordinator
    coordinator.hass.config_entries.async_reload = AsyncMock()
    for level in (3, 0, 1, 2):
        coordinator.entry.options = {"diagnostic_level": level}
        await integration._async_options_updated(coordinator.hass, coordinator.entry)
        assert control._leases[1] is lease and control._watchdogs[1] is watchdog
        assert coordinator.control_transport.write_calls == writes
        assert control.remote_owner(1) == OWNER
    coordinator.hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_actual_channel_change_emits_one_capability_event(
    monkeypatch, clock, caplog
):
    from tests.test_sensor import _string_words

    coordinator, _, client, bases = endpoint_runtime(
        monkeypatch, clock, level=2, models=((160, 88),)
    )
    await coordinator.async_refresh()
    for index, word in enumerate(_string_words("MPPT1", 8)):
        client.units[1][bases[160] + 9 + index] = word
    for now in (5, 10, 15):
        clock.now = now
        await coordinator.async_refresh()
    assert len(records(caplog, "capabilities_changed")) == 1
    assert coordinator.measurement_epochs[1] == 1


@pytest.mark.asyncio
async def test_diagnostics_cannot_restore_expired_write_authority(caplog):
    from custom_components.fronius_pv_manager.write_runtime import (
        WriteModelNotDiscoveredError,
    )

    coor, control = controller()
    coor.entry.options["diagnostic_level"] = 3
    assert coor.write_authority(1) is not None
    sensor = raw_sensor(coor, 124, "ChaState")
    coor._discovery_deadlines[1] = -1
    assert coor.write_authority(1) is None
    assert sensor.available
    with pytest.raises(WriteModelNotDiscoveredError):
        await coor.write_runtime.async_write(1, 124, "MinRsvPct", 20)
    assert not coor.control_transport.write_calls
    assert records(caplog, "control_failed")
    assert coor.write_authority(1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 3])
async def test_upstream_timeout_logs_are_local_and_have_no_frame_dumps(
    monkeypatch, clock, caplog, level
):
    from pymodbus.exceptions import ModbusIOException
    from pymodbus.logging import Log

    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=level)
    await coordinator.async_refresh()

    def timeout(*args, **kwargs):
        Log.error("No response received after 0 retries, continue with next request")
        raise ModbusIOException(
            "No response received after 0 retries, continue with next request"
        )

    monkeypatch.setattr(client, "read_holding_registers", timeout)
    for now in (5, 10, 15):
        clock.now = now
        await coordinator.async_refresh()
    assert not [r for r in caplog.records if r.name == "pymodbus.logging"]
    warnings = records(caplog, "communication_failure")
    assert len(warnings) == (1 if level else 0)
    if warnings:
        assert warnings[0]["reason"] == "timeout"
    # An unrelated client outside the request context retains its normal logger.
    upstream = logging.getLogger("pymodbus.logging")
    upstream.error("unrelated client error")
    assert any(r.name == "pymodbus.logging" for r in caplog.records)


@pytest.mark.asyncio
async def test_unknown_upstream_error_is_safe_and_visible_at_zero(
    monkeypatch, clock, caplog
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=0)
    await coordinator.async_refresh()
    read = client.read_holding_registers

    def faulty(*args, **kwargs):
        logging.getLogger("pymodbus.logging").error(
            "unexpected defect: SECRET FULL FRAME"
        )
        return read(*args, **kwargs)

    monkeypatch.setattr(client, "read_holding_registers", faulty)
    for now in (5, 10):
        clock.now = now
        await coordinator.async_refresh()
    assert len(records(caplog, "upstream_error")) == 1
    assert records(caplog, "upstream_error")[0]["severity"] == "ERROR"
    assert "SECRET FULL FRAME" not in caplog.text
    assert coordinator.data.devices[0].available


@pytest.mark.asyncio
async def test_response_identity_violation_is_level_zero_protocol_error(
    monkeypatch, clock, caplog
):
    from pymodbus.exceptions import ModbusIOException

    coordinator, _, client, bases = endpoint_runtime(monkeypatch, clock, level=0)
    await coordinator.async_refresh()
    client.errors[(1, bases[103])] = ModbusIOException(
        "ERROR: request uses device id=1 but received 200."
    )
    clock.now = 5
    await coordinator.async_refresh()
    assert (
        records(caplog, "protocol_error")[0]["reason"] == "response_identity_mismatch"
    )
    assert records(caplog, "protocol_error")[0]["severity"] == "ERROR"


@pytest.mark.asyncio
async def test_duplicate_models_cannot_invent_periodic_state_transitions(
    monkeypatch, clock, caplog
):
    from custom_components.fronius_pv_manager.register_maps import MODEL_103

    coordinator, _, client, bases = endpoint_runtime(
        monkeypatch, clock, level=2, models=((103, 50), (103, 50))
    )
    offset = next(r.offset for r in MODEL_103.registers if r.name == "St")
    client.units[1][bases[103] + offset] = 4
    for now in (0, 5, 10, 15):
        clock.now = now
        await coordinator.async_refresh()
    assert not records(caplog, "operating_state_changed")


@pytest.mark.asyncio
async def test_validated_empty_topology_has_transition_but_no_write_authority(
    monkeypatch, clock, caplog
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, level=2)
    await coordinator.async_refresh()
    client.units[1], _ = model_chain()
    clock.now = 300
    await coordinator.async_refresh()
    assert len(records(caplog, "topology_changed")) == 1
    assert coordinator.write_authority(1) is None
    assert not coordinator.measurement_data.devices[0].available
