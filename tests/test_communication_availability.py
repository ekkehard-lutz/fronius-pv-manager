"""Per-device communication expiry on existing reads; no hardware or new I/O."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError

import custom_components.fronius_pv_manager as integration
from custom_components.fronius_pv_manager.communication import communication_timeout
from custom_components.fronius_pv_manager.const import (
    CONF_COMMUNICATION_AVAILABILITY_TIMEOUT as TIMEOUT,
)
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    SolarConsumptionSensor,
    SolarPowerSensor,
    _sensor_sources,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain
from tests.test_diagnostic_logging import endpoint_runtime, records
from tests.test_measurement_publication import raw_sensor
from tests.test_polling import clock as clock_fixture
from tests.test_polling import runtime
from tests.test_polling_options import options_flow

clock = clock_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [30, 300, 600])
async def test_option_bounds_and_preservation(timeout):
    original = {
        "fast_scan_interval": 7,
        "slow_scan_interval": 300,
        "diagnostic_level": 2,
        "unrelated": "keep",
    }
    flow, entry = options_flow(original)
    assert (await flow.async_step_init())["data_schema"]({})[TIMEOUT] == 300
    result = await flow.async_step_init({TIMEOUT: timeout})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**original, TIMEOUT: timeout}
    entry.options = result["data"]
    assert (await flow.async_step_init())["data_schema"]({})[TIMEOUT] == timeout
    result = await flow.async_step_init({"diagnostic_level": 1})
    assert result["data"][TIMEOUT] == timeout


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [29, 601, 0, -1, True, False, 30.5, "300", None])
async def test_invalid_timeout_is_not_persisted(invalid):
    flow, entry = options_flow({TIMEOUT: 600})
    result = await flow.async_step_init({TIMEOUT: invalid})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {TIMEOUT: "invalid_communication_timeout"}
    assert entry.options == {TIMEOUT: 600}
    assert communication_timeout({TIMEOUT: invalid}) == 300


def test_translations_and_default():
    assert communication_timeout({}) == 300
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        data = json.loads(
            (Path("custom_components/fronius_pv_manager") / name).read_text()
        )
        step = data["options"]["step"]["init"]
        assert step["data"][TIMEOUT]
        assert "300" in step["data_description"][TIMEOUT]
        assert data["options"]["error"]["invalid_communication_timeout"]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 30, 600])
@pytest.mark.parametrize("recovered_value", [123, 456])
async def test_expiry_boundary_once_and_real_ha_reports(
    clock, tmp_path, timeout, recovered_value
):
    options = {} if timeout is None else {TIMEOUT: timeout}
    threshold = 300 if timeout is None else timeout
    coordinator, transport, bases = runtime((103, 50), options=options)
    transport.registers[bases[103] + 12] = 123
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    snapshot = entity._source_snapshot()
    hass = HomeAssistant(str(tmp_path))
    reports = []

    def report():
        reports.append((entity.available, entity.native_value))
        hass.states.async_set(
            "sensor.communication",
            str(entity.native_value) if entity.available else "unavailable",
        )

    entity.async_write_ha_state = report
    report()
    initial = hass.states.get("sensor.communication")
    transport.fail_reads = True
    for now in (5, 5 + threshold - 1):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available and entity.native_value == 123
        assert (
            hass.states.get("sensor.communication").last_reported
            == initial.last_reported
        )
        assert coordinator.write_authority(1) is None
        with pytest.raises(ServiceValidationError):
            coordinator.storage_control.snapshot(1)
    for now in (5 + threshold, 10 + threshold, 50 + threshold):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert not entity.available and entity.native_value == 123
        assert entity._source_snapshot() is snapshot
        assert reports == [(True, 123), (False, 123)]
    expired = hass.states.get("sensor.communication")
    assert expired.state == "unavailable"
    assert coordinator.communication_timeout_revisions == {1: 1}
    transport.fail_reads = False
    transport.registers[bases[103] + 12] = recovered_value
    clock.now = threshold + 55
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert entity.available and entity.native_value == recovered_value
    assert reports == [(True, 123), (False, 123), (True, recovered_value)]
    assert hass.states.get("sensor.communication").last_reported > expired.last_reported
    assert coordinator.write_authority(1) is not None
    assert not coordinator.communication_expired


@pytest.mark.asyncio
async def test_short_outage_and_unchanged_success_reset_interval(clock):
    coordinator, transport, _ = runtime((103, 50), options={TIMEOUT: 30})
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    for now, failed in [(5, True), (34, False), (40, True), (69, True), (70, True)]:
        clock.now = now
        transport.fail_reads = failed
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available is (now < 70)
    assert entity.async_write_ha_state.call_count == 2  # Fresh unchanged read + expiry.


@pytest.mark.asyncio
@pytest.mark.parametrize("models", [((1, 65), (103, 50), (122, 44)), ((122, 44),)])
async def test_slow_300_does_not_cause_false_timeout_or_extra_reads(clock, models):
    coordinator, transport, bases = runtime(
        *models, options={TIMEOUT: 30, "slow_scan_interval": 300}
    )
    for now in range(0, 601, 5):
        clock.now = now
        await coordinator.async_refresh()
        assert not coordinator.communication_expired
        assert not coordinator._communication_failures
    # Two discovery sequences (initial and scheduled), with no timeout traffic.
    headers = len(models) + 2
    assert len(transport.read_calls) == headers * 3 + sum(
        121 if model == 103 else 3 for model, _ in models
    )
    assert all(d.available for d in coordinator.measurement_data.devices)
    assert bases


@pytest.mark.asyncio
async def test_individual_model_failure_does_not_expire_device(clock):
    coordinator, transport, bases = runtime((103, 50), (124, 24), options={TIMEOUT: 30})
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    read = transport.read_holding_registers

    def partial(address, count):
        if address == bases[103]:
            raise ModbusTransportError("failed model")
        return read(address, count)

    transport.read_holding_registers = partial
    for now in (5, 35, 100, 700):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available
        assert not coordinator.communication_expired
        assert coordinator.write_authority(1) is None
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_successful_chunk_is_communication_even_if_later_chunk_fails(clock):
    coordinator, transport, bases = runtime((203, 105), options={TIMEOUT: 30})
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator, 203)
    read = transport.read_holding_registers

    def partial(address, count):
        if address == bases[203] + 100:
            raise ModbusTransportError("second chunk failed")
        return read(address, count)

    transport.read_holding_registers = partial
    for now in (5, 35, 100, 700):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available
        assert coordinator._communication_failures[1] == now
        assert not coordinator.communication_expired
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_shared_endpoint_unit_200_and_derived_dependencies(
    clock, monkeypatch, caplog, level
):
    coordinator, endpoint, client, bases = endpoint_runtime(
        monkeypatch, clock, models=((103, 50), (160, 88)), units=(1, 200), level=level
    )
    from tests.test_sensor import _string_words

    for index, word in enumerate(_string_words("MPPT 1", 8)):
        client.units[1][bases[160] + 9 + index] = word
    coordinator.entry.options[TIMEOUT] = 30
    meter_registers, meter_bases = model_chain((203, 105))
    client.units[200] = meter_registers
    client.units[1][bases[103] + 12] = 100
    client.units[200][meter_bases[203] + 16] = 200
    await coordinator.async_refresh()
    inverter = raw_sensor(coordinator)
    meter = raw_sensor(coordinator, 203)
    consumption = SolarConsumptionSensor(coordinator, "test", 1, "consumption_power")
    grid = SolarPowerSensor(coordinator, "test", 200, "grid_import_power", "meter")
    pv = SolarPowerSensor(coordinator, "test", 1, "pv_power", "inverter")
    for entity in (consumption, grid, pv):
        entity.async_write_ha_state = Mock()
        entity._handle_coordinator_update()
        entity.async_write_ha_state.reset_mock()
    assert consumption.native_value == 300 and grid.native_value == 200
    client.errors[200] = TimeoutError()
    for now in (5, 34, 35, 40, 100):
        clock.now = now
        await coordinator.async_refresh()
        for entity in (inverter, meter, consumption, grid, pv):
            entity._handle_coordinator_update()
        assert inverter.available and pv.available
        assert meter.available is (now < 35)
        assert consumption.available is (now < 35)
        assert grid.available is (now < 35)
        assert consumption.native_value == 300
        assert coordinator.write_authority(200) is None
    assert coordinator.communication_expired == {200}
    assert (
        client.connect_calls > 1
    )  # Healthy shared TCP sessions cannot clear unit 200.
    for entity in (meter, consumption, grid):
        entity.async_write_ha_state.assert_called_once()
    events = records(caplog, "communication_availability_timeout")
    assert len(events) == (1 if level else 0)
    if events:
        assert events[0]["device_id"] == 200
        assert events[0]["timeout_seconds"] == 30
    client.errors.clear()
    clock.now = 105
    await coordinator.async_refresh()
    for entity in (meter, consumption, grid):
        entity._handle_coordinator_update()
        assert entity.available
        assert entity.async_write_ha_state.call_count == 2
    recovery = records(caplog, "communication_availability_recovered")
    assert len(recovery) == (1 if level else 0)
    if recovery:
        assert recovery[0]["device_id"] == 200 and recovery[0]["timeout_seconds"] == 30
    assert not coordinator.communication_expired
    assert endpoint.generation > 1


@pytest.mark.asyncio
async def test_discovery_recovery_does_not_restore_failed_source(clock):
    coordinator, transport, bases = runtime((203, 105), options={TIMEOUT: 30})
    await coordinator.async_refresh()
    raw = raw_sensor(coordinator, 203)
    derived = SolarPowerSensor(coordinator, "test", 1, "grid_import_power", "meter")
    derived.async_write_ha_state = Mock()
    derived._handle_coordinator_update()
    assert derived.available
    transport.fail_reads = True
    for now in (5, 35):
        clock.now = now
        await coordinator.async_refresh()
        raw._handle_coordinator_update()
        derived._handle_coordinator_update()
    assert not raw.available and not derived.available
    transport.fail_reads = False
    read = transport.read_holding_registers

    def failed_payload(address, count):
        if address == bases[203] + 100:
            raise ModbusTransportError("payload still failing")
        return read(address, count)

    transport.read_holding_registers = failed_payload
    clock.now = 40
    await coordinator.async_refresh()
    raw._handle_coordinator_update()
    derived._handle_coordinator_update()
    # Discovery/first chunk prove communication, not valid full source data.
    assert not coordinator.communication_expired
    assert not raw.available and raw.native_value == 0
    assert not derived.available and derived.native_value == 0
    transport.read_holding_registers = read
    clock.now = 45
    await coordinator.async_refresh()
    raw._handle_coordinator_update()
    derived._handle_coordinator_update()
    assert raw.available and derived.available


@pytest.mark.asyncio
async def test_offline_startup_never_borrows_cached_values(clock):
    old, _, _ = runtime((103, 50))
    await old.async_refresh()
    entry = FakeEntry({"topology": old.topology})
    entry.options = {TIMEOUT: 30}
    transport = FakeTransport({})
    transport.fail_reads = True
    coordinator = FroniusPVCoordinator(FakeHass(), entry, {1: transport})
    source = next(s for s in _sensor_sources(coordinator) if s.register_name == "W")
    entity = FroniusPVSensor(coordinator, "test", source)
    entity.async_write_ha_state = Mock()
    for now in (0, 29, 30, 60):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert not entity.available and entity.native_value is None
        assert coordinator.write_authority(1) is None
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_live_option_keeps_runtime_schedule_caches_and_leases(clock):
    coordinator, transport, _ = runtime((103, 50))
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    entry, hass = coordinator.entry, coordinator.hass
    entry.runtime_data = coordinator
    hass.config_entries.async_reload = AsyncMock()
    cache = coordinator.measurement_data
    deadlines = dict(coordinator._slow_deadlines)
    calls = list(transport.read_calls)
    lease = object()
    coordinator.storage_control._leases[1] = lease
    entry.options[TIMEOUT] = 30
    await integration._async_options_updated(hass, entry)
    assert coordinator.measurement_data is cache
    assert coordinator._slow_deadlines == deadlines
    assert transport.read_calls == calls
    assert coordinator.storage_control._leases[1] is lease
    hass.config_entries.async_reload.assert_not_called()
    coordinator.storage_control._leases.clear()
    transport.fail_reads = True
    for now in (5, 35):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
    assert not entity.available
    # Raising the timeout cannot revive an old source without a valid response.
    entry.options[TIMEOUT] = 600
    await integration._async_options_updated(hass, entry)
    clock.now = 40
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available


@pytest.mark.asyncio
async def test_storage_status_expires_but_http_status_is_independent(clock):
    from custom_components.fronius_pv_manager.binary_sensor import SolarBinarySensor
    from custom_components.fronius_pv_manager.control_entity import (
        control_entity_sources,
    )
    from custom_components.fronius_pv_manager.models import EntityPlatform
    from custom_components.fronius_pv_manager.storage_status import StorageControlStatus

    coordinator, transport, _ = runtime((124, 24), options={TIMEOUT: 30})
    await coordinator.async_refresh()
    source = next(
        s
        for s in control_entity_sources(coordinator, EntityPlatform.NUMBER)
        if s.register_name == "MinRsvPct"
    )
    status = StorageControlStatus(coordinator, "test", source, "control_status")
    status.async_write_ha_state = Mock()
    status._handle_coordinator_update()
    held = status.native_value
    status.async_write_ha_state.reset_mock()
    http = SolarBinarySensor(coordinator, "test", 1, "backup_mode", "inverter")
    # The HTTP source has its own successful poll and availability.
    coordinator.solar_api.async_poll = AsyncMock(
        return_value=replace(coordinator.solar_state, backup_mode=True)
    )
    transport.fail_reads = True
    for now in (5, 35, 40):
        clock.now = now
        await coordinator.async_refresh()
        status._handle_coordinator_update()
        assert status.available is (now < 35)
        assert status.native_value == held
        assert http.available and http.is_on is True
        assert coordinator.write_authority(1) is None
    status.async_write_ha_state.assert_called_once()
    transport.fail_reads = False
    clock.now = 45
    await coordinator.async_refresh()
    status._handle_coordinator_update()
    assert status.available and status.native_value == held
    assert status.async_write_ha_state.call_count == 2


@pytest.mark.asyncio
async def test_efficiency_timeout_does_not_reset_window_or_publish_early(
    clock, monkeypatch
):
    from dataclasses import replace

    from custom_components.fronius_pv_manager.sensor import SolarEfficiencySensor
    from tests.test_efficiency_sensors import sources

    coordinator, transport, _ = runtime(
        (103, 50),
        (120, 26),
        (124, 24),
        (160, 88),
        options={TIMEOUT: 30, "slow_scan_interval": 300},
    )
    original = coordinator._poll_device

    def poll(view, discovered):
        values = {s.discovered.model_id: s for s in sources()}
        return tuple(
            replace(values[s.discovered.model_id], discovered=s.discovered)
            if s.available
            else s
            for s in original(view, discovered)
        )

    monkeypatch.setattr(coordinator, "_poll_device", poll)
    await coordinator.async_refresh()
    entity = SolarEfficiencySensor(
        coordinator, "test", 1, "inverter_efficiency", "inverter"
    )
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    assert entity.native_value == 96
    window = entity._window
    samples = list(window.samples)
    entity.async_write_ha_state.reset_mock()
    transport.fail_reads = True
    for now in (5, 35, 40):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available is (now < 35)
        assert entity._window is window
        assert list(window.samples) == samples
        assert entity.native_value == 96
    entity.async_write_ha_state.assert_called_once()
    transport.fail_reads = False
    clock.now = 45
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert entity.available and entity.native_value == 96
    assert entity._window is window and len(window.samples) == 2
    assert not coordinator.slow_publication_devices
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.asyncio
async def test_missing_individual_derived_source_keeps_value_and_unit_healthy(clock):
    coordinator, transport, bases = runtime(
        (103, 50), (203, 105), options={TIMEOUT: 30}
    )
    await coordinator.async_refresh()
    entity = SolarConsumptionSensor(coordinator, "test", 1, "consumption_power")
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    assert entity.native_value == 0
    entity.async_write_ha_state.reset_mock()
    # A valid Modbus payload with a SunSpec not-implemented individual reading.
    transport.registers[bases[203] + 16] = 0x8000
    for now in (5, 35, 700):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert not coordinator._communication_failures
        assert entity.available and entity.native_value == 0
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_live_lowering_expires_existing_failure_on_next_refresh(clock):
    coordinator, transport, _ = runtime((103, 50), options={TIMEOUT: 600})
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    transport.fail_reads = True
    for now in (5, 35):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available
    coordinator.entry.runtime_data = coordinator
    coordinator.hass.config_entries.async_reload = AsyncMock()
    coordinator.entry.options[TIMEOUT] = 30
    calls = list(transport.read_calls)
    await integration._async_options_updated(coordinator.hass, coordinator.entry)
    assert transport.read_calls == calls
    clock.now = 36
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available
    coordinator.hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_shared_endpoint_each_unit_keeps_own_failure_start(clock, monkeypatch):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock, units=(1, 200))
    coordinator.entry.options[TIMEOUT] = 30
    await coordinator.async_refresh()
    for now in (5, 20, 35, 50):
        client.errors[200] = TimeoutError()
        if now >= 20:
            client.errors[1] = TimeoutError()
        clock.now = now
        await coordinator.async_refresh()
        assert coordinator.communication_expired == (
            {1, 200} if now == 50 else {200} if now == 35 else set()
        )
    assert coordinator._communication_failures == {1: 20, 200: 5}


@pytest.mark.asyncio
async def test_new_availability_episode_within_300_seconds_is_visible(
    clock, monkeypatch, caplog
):
    coordinator, _, client, _ = endpoint_runtime(monkeypatch, clock)
    coordinator.entry.options[TIMEOUT] = 30
    await coordinator.async_refresh()
    for start in (5, 45):
        client.errors[1] = TimeoutError()
        for now in (start, start + 30):
            clock.now = now
            await coordinator.async_refresh()
        client.errors.clear()
        clock.now = start + 35
        await coordinator.async_refresh()
    assert len(records(caplog, "communication_availability_timeout")) == 2
    assert len(records(caplog, "communication_availability_recovered")) == 2
