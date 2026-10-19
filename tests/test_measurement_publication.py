"""Deterministic sensor publication tests; no hardware or physical writes."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant, callback

from custom_components.fronius_pv_manager.const import CONF_SLOW_SCAN_INTERVAL
from custom_components.fronius_pv_manager.coordinator import (
    DeviceSnapshot,
    FroniusPVCoordinator,
    FroniusPVCoordinatorData,
)
from custom_components.fronius_pv_manager.measurement import (
    EfficiencyWindow,
    normalize_measurement,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_103
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    SolarConsumptionSensor,
    SolarEfficiencySensor,
    SolarPowerSensor,
    _sensor_sources,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain
from tests.test_efficiency_sensors import change, sources
from tests.test_polling import clock as clock_fixture
from tests.test_polling import runtime
from tests.test_rectifier_efficiency import rectifier
from tests.test_sensor import _coordinator_with, _snapshot

clock = clock_fixture


@pytest.mark.parametrize(
    "unit,value,expected",
    [
        ("V", "230.05", 230.1),
        ("V", "-230.05", -230.1),
        ("V", "230.04999", 230.0),
        ("V", "-230.04999", -230.0),
        ("A", "1.2345", 1.235),
        ("A", "-1.2345", -1.235),
        ("A", "0.0005", 0.001),
        ("A", "-0.0005", -0.001),
        ("W", "12.25", 12.3),
        ("W", "-12.25", -12.3),
        ("W", "-0.049", 0.0),
        ("W", "-0.05", -0.1),
    ],
)
def test_decimal_half_up(unit, value, expected):
    assert normalize_measurement(Decimal(value), unit) == expected
    assert normalize_measurement(float(value), unit) == expected


@pytest.mark.parametrize("unit", ["Wh", "%", "Hz", "C", "var", "VA", None])
def test_other_dimensions_retain_precision(unit):
    value = Decimal("123456789.123456789")
    assert normalize_measurement(value, unit) is value


@pytest.mark.parametrize(
    "values,expected",
    [
        ([95, 96, 999], 96),
        ([94, 96, 98, 999], 97),
        ([Decimal("95.05")], 95.1),
        ([0, 1000, 96, 96, 96], 96),
    ],
)
def test_efficiency_median(values, expected):
    window = EfficiencyWindow()
    for now, value in enumerate(values):
        window.observe(now, value, "inverter")
    assert window.publish() == expected
    assert window.publish() is None


def test_rolling_window_mode_and_long_pause():
    window = EfficiencyWindow()
    window.observe(0, 10, "inverter")
    window.observe(60, 90, "inverter")
    assert window.publish() == 50  # Closed interval includes t=0 at t=60.
    window.observe(60.1, 100, "inverter")
    assert window.publish() == 95
    window.observe(65, None, "rectifier")
    assert not window.samples
    window.observe(70, 30, "rectifier")
    assert window.publish() == 30
    window.observe(200, None, None)
    assert not window.samples and window.publish() is None
    window.observe(205, 80, "rectifier")
    assert window.publish() == 80


def raw_sensor(coordinator, model=103, register="W"):
    source = next(
        s
        for s in _sensor_sources(coordinator)
        if s.model_id == model and s.register_name == register
    )
    entity = FroniusPVSensor(coordinator, "test", source)
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.reset_mock()
    return entity


@pytest.mark.asyncio
async def test_normalized_reports_and_real_ha_events(clock, tmp_path):
    coordinator, transport, bases = runtime((103, 50))
    base = bases[103]
    transport.registers[base + 13] = 0xFFFE  # W_SF=-2
    transport.registers[base + 12] = 1231
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    hass = HomeAssistant(str(tmp_path))
    events = {"state_changed": [], "state_reported": []}

    @callback
    def filtered(data):
        return data["entity_id"] == "sensor.power"

    for event_type in events:

        @callback
        def capture(event, kind=event_type):
            events[kind].append(event)

        hass.bus.async_listen(event_type, capture, event_filter=filtered)
    entity.async_write_ha_state = lambda: hass.states.async_set(
        "sensor.power", str(entity.native_value), {"unit_of_measurement": "W"}
    )
    entity.async_write_ha_state()
    original = hass.states.get("sensor.power")
    reported_at = original.last_reported
    assert entity.native_value == 12.3
    for now, word, expected in [(5, 1234, 12.3), (10, 1235, 12.4)]:
        clock.now = now
        transport.registers[base + 12] = word
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        state = hass.states.get("sensor.power")
        assert entity.native_value == expected
        assert state.last_reported > reported_at
        if now == 5:
            assert state.last_changed == original.last_changed
            assert state.last_updated == original.last_updated
    await hass.async_block_till_done()
    assert len(events["state_changed"]) == 2
    assert len(events["state_reported"]) == 1
    # Internal decoded value has not been rounded.
    assert coordinator.data.decoded_models[0].decoded.fixed["W"].value == 12.35


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,length,register,offset",
    [
        (103, 50, "W", 12),
        (203, 105, "W", 16),
        (122, 44, "WAval", 29),
    ],
)
async def test_repeated_timeouts_hold_raw_and_do_not_report(
    clock, model, length, register, offset
):
    coordinator, transport, bases = runtime((model, length))
    transport.registers[bases[model] + offset] = 123
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator, model, register)
    old = entity._source_snapshot()
    assert entity.native_value == 123
    transport.fail_reads = True
    for now in (30, 35, 40):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available and entity.native_value == 123
        assert entity._source_snapshot() is old
        assert coordinator.write_authority(1) is None
        entity.async_write_ha_state.assert_not_called()
    transport.fail_reads = False
    for now, word in [(45, 123), (50, 456)]:
        clock.now = now
        transport.registers[bases[model] + offset] = word
        # SLOW recovery is full; subsequent SLOW change waits for its cadence.
        if model == 122:
            coordinator._force_slow = True
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        assert entity.available and entity.native_value == word
        entity.async_write_ha_state.assert_called_once()
        entity.async_write_ha_state.reset_mock()
        assert coordinator.write_authority(1) is not None


@pytest.mark.asyncio
async def test_second_chunk_failure_and_sibling_success(clock):
    coordinator, transport, bases = runtime((103, 50), (203, 105))
    await coordinator.async_refresh()
    meter = raw_sensor(coordinator, 203)
    inverter = raw_sensor(coordinator)
    read = transport.read_holding_registers

    def fail(address, count):
        if address == bases[203] + 100:
            raise ModbusTransportError("second chunk timeout")
        return read(address, count)

    transport.read_holding_registers = fail
    clock.now = 5
    transport.registers[bases[203] + 16] = 100
    transport.registers[bases[103] + 12] = 200
    await coordinator.async_refresh()
    for sensor in (meter, inverter):
        sensor._handle_coordinator_update()
    assert meter.native_value == 0
    meter.async_write_ha_state.assert_not_called()
    assert inverter.native_value == 200
    inverter.async_write_ha_state.assert_called_once()
    assert not coordinator.data.decoded_models[1].available


@pytest.mark.asyncio
async def test_initial_failure_and_incompatible_topology(clock):
    coordinator, transport, bases = runtime((103, 50))
    await coordinator.async_discover()
    transport.fail_reads = True
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    assert not entity.available and entity.native_value is None
    transport.fail_reads = False
    clock.now = 5
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert entity.available
    transport.registers[40000] = 0  # Proven invalid SunSpec signature.
    coordinator.discovered_models_by_device.clear()
    clock.now = 10
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available and coordinator.write_authority(1) is None
    # A later transport outage must not resurrect the invalidated measurement.
    transport.fail_reads = True
    clock.now = 15
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available


@pytest.mark.asyncio
async def test_new_topology_cannot_borrow_prior_model(clock):
    coordinator, transport, bases = runtime((103, 50), (124, 24))
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    registers, new_bases = model_chain((124, 24), (103, 50))
    transport.registers = registers
    read = transport.read_holding_registers

    def fail(address, count):
        if address == new_bases[103]:
            raise ModbusTransportError("new model payload failed")
        return read(address, count)

    transport.read_holding_registers = fail
    coordinator.discovered_models_by_device.clear()
    clock.now = 5
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available and entity.native_value is None
    assert coordinator.measurement_epochs[1] == 1


@pytest.mark.asyncio
async def test_generation_reset_and_two_units_isolate_values(clock):
    coordinator, first, _ = runtime((103, 50))
    registers, _ = model_chain((103, 50))
    second = FakeTransport(registers)
    owner = SimpleNamespace(generation=1, connect=lambda: None)

    def view(transport):
        return SimpleNamespace(
            endpoint=owner, read_holding_registers=transport.read_holding_registers
        )

    coordinator = FroniusPVCoordinator(
        FakeHass(), FakeEntry({}), {1: view(first), 2: view(second)}
    )
    await coordinator.async_refresh()
    source = next(
        s
        for s in _sensor_sources(coordinator)
        if s.device_id == 1 and s.register_name == "W"
    )
    entity = FroniusPVSensor(coordinator, "test", source)
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.reset_mock()
    owner.generation += 1
    first.fail_reads = True
    clock.now = 5
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert entity.available and entity.native_value == 0
    entity.async_write_ha_state.assert_not_called()
    assert coordinator.write_authority(1) is None
    assert coordinator.write_authority(2) is not None
    first.fail_reads = False
    clock.now = 10
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.assert_called_once()
    assert coordinator.write_authority(1)[0] == owner.generation


def publish_sources(coordinator, entity, snapshots, now, slow=False):
    coordinator.data = FroniusPVCoordinatorData(
        (DeviceSnapshot(1, tuple(s.discovered for s in snapshots), tuple(snapshots)),)
    )
    coordinator.measurement_time = now
    coordinator.slow_publication_devices = {1} if slow else set()
    entity._handle_coordinator_update()


@pytest.mark.parametrize(
    "key,role",
    [
        ("inverter_efficiency", "inverter"),
        ("rectifier_efficiency", "inverter"),
        ("battery_lifetime_efficiency", "storage"),
    ],
)
def test_efficiencies_only_publish_on_slow_and_never_refill_held(key, role):
    snapshots = rectifier() if key == "rectifier_efficiency" else sources()
    coordinator, _, _ = _coordinator_with(*snapshots)
    entity = SolarEfficiencySensor(coordinator, "test", 1, key, role)
    entity.async_write_ha_state = Mock()
    publish_sources(coordinator, entity, snapshots, 0, slow=True)
    initial = entity.native_value
    assert initial is not None
    entity.async_write_ha_state.reset_mock()
    for now in (5, 10, 15):
        fresh = tuple(replace(s) for s in snapshots)
        publish_sources(coordinator, entity, fresh, now)
        assert entity.native_value == initial
    assert len(entity._window.samples) == 4
    # Identical retained snapshot callbacks, including a failed SLOW cycle.
    failed = tuple(replace(s, available=False, read_failed=True) for s in snapshots)
    for now in (20, 25, 30):
        publish_sources(coordinator, entity, failed, now, slow=(now == 30))
    assert len(entity._window.samples) == 4
    assert entity.native_value == initial
    entity.async_write_ha_state.assert_not_called()
    publish_sources(coordinator, entity, failed, 200, slow=True)
    assert not entity._window.samples and entity.native_value == initial
    fresh = tuple(replace(s) for s in snapshots)
    publish_sources(coordinator, entity, fresh, 205)
    assert len(entity._window.samples) == 1
    entity.async_write_ha_state.assert_not_called()


def test_median_outlier_mode_switch_and_rounding_after_median():
    coordinator, _, _ = _coordinator_with(*sources())
    entity = SolarEfficiencySensor(
        coordinator, "test", 1, "inverter_efficiency", "inverter"
    )
    entity.async_write_ha_state = Mock()
    for now, ac in [(0, 4700), (5, 4800), (10, 30000), (15, 4800), (30, 4800)]:
        publish_sources(coordinator, entity, sources(ac=ac), now, slow=now in (0, 30))
        assert entity.native_value == (94 if now < 30 else 96)
    assert entity.async_write_ha_state.call_count == 2
    publish_sources(coordinator, entity, rectifier(), 35)
    assert not entity._window.samples
    publish_sources(coordinator, entity, sources(ac=4500), 40)
    assert len(entity._window.samples) == 1
    publish_sources(coordinator, entity, sources(ac=4600), 60, slow=True)
    assert entity.native_value == 91
    # 94.04/94.05 median=94.045 -> 94.0; rounding samples first would give 94.1.
    window = EfficiencyWindow()
    window.observe(0, 94.04, None)
    window.observe(5, 94.05, None)
    assert window.publish() == 94.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fast,slow,expected",
    [
        (5, 30, [0, 30, 60]),
        (7, 30, [0, 35, 63]),
        (5, 60, [0, 60]),
    ],
)
async def test_efficiency_uses_normal_slow_deadlines(
    clock, monkeypatch, fast, slow, expected
):
    coordinator, _, _ = runtime(
        (103, 50),
        (120, 26),
        (124, 24),
        (160, 88),
        options={"fast_scan_interval": fast, CONF_SLOW_SCAN_INTERVAL: slow},
    )
    original = coordinator._poll_device

    def poll(transport, discovered):
        values = {s.discovered.model_id: s for s in sources(ac=4700 + clock.now)}
        return tuple(
            replace(values[s.discovered.model_id], discovered=s.discovered)
            for s in original(transport, discovered)
        )

    monkeypatch.setattr(coordinator, "_poll_device", poll)
    entity = SolarEfficiencySensor(
        coordinator, "test", 1, "inverter_efficiency", "inverter"
    )
    reports = []
    entity.async_write_ha_state = lambda: reports.append(clock.now)
    for now in range(0, 70, fast):
        clock.now = now
        await coordinator.async_refresh()
        entity._handle_coordinator_update()
        if now == fast:
            # An explicit recovery/readback full poll is not a SLOW publication.
            coordinator._force_slow = True
    assert reports == expected


def test_derived_partial_failure_keeps_complete_result():
    inverter = _snapshot(MODEL_103, [0] * 50)
    coordinator, _, _ = _coordinator_with(inverter)
    # Use real helpers to supply a meter model with import=0.
    from custom_components.fronius_pv_manager.register_maps import MODEL_203

    meter = _snapshot(MODEL_203, [0] * 105)
    coordinator = FroniusPVCoordinator(
        FakeHass(), FakeEntry({}), {1: FakeTransport({}), 200: FakeTransport({})}
    )
    coordinator.data = FroniusPVCoordinatorData(
        (
            DeviceSnapshot(1, (inverter.discovered,), (inverter,)),
            DeviceSnapshot(200, (meter.discovered,), (meter,)),
        )
    )
    entity = SolarConsumptionSensor(coordinator, "test", 1, "consumption_power")
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    assert entity.native_value == 0
    entity.async_write_ha_state.reset_mock()
    changed = change((inverter,), 103, "W", 1000)[0]
    coordinator.data = replace(
        coordinator.data,
        devices=(
            replace(coordinator.data.devices[0], decoded_models=(changed,)),
            replace(
                coordinator.data.devices[1],
                decoded_models=(replace(meter, available=False),),
            ),
        ),
    )
    entity._handle_coordinator_update()
    assert entity.available and entity.native_value == 0
    entity.async_write_ha_state.assert_not_called()


def test_semantic_rounding_never_changes_formula_or_direction():
    from tests.test_power_sensors import mppts

    coordinator, _, _ = _coordinator_with(mppts([("MPPT 1", 10)]))
    snapshot = coordinator.data.decoded_models[0]
    values = dict(snapshot.decoded.repeating["module"][0].values)
    values["DCW"] = replace(values["DCW"], value=10.05)
    module = replace(snapshot.decoded.repeating["module"][0], values=values)
    snapshot = replace(
        snapshot, decoded=replace(snapshot.decoded, repeating={"module": (module,)})
    )
    coordinator.data = replace(
        coordinator.data,
        devices=(replace(coordinator.data.devices[0], decoded_models=(snapshot,)),),
    )
    entity = SolarPowerSensor(coordinator, "test", 1, "pv_power", "inverter")
    assert entity.value == 10.05 and entity.native_value == 10.1


@pytest.mark.asyncio
async def test_energy_and_control_parameters_unrounded(clock):
    coordinator, transport, bases = runtime((103, 50), (124, 24))
    # WH uint32 + SF=-2; WChaMax SF=-2. Both retain original decoded precision.
    base = bases[103]
    transport.registers[base + 23] = 12345
    transport.registers[base + 24] = 0xFFFE
    transport.registers[bases[124]] = 12345
    transport.registers[bases[124] + 16] = 0xFFFE
    await coordinator.async_refresh()
    for model, name in [(103, "WH"), (124, "WChaMax")]:
        entity = raw_sensor(coordinator, model, name)
        snapshot = next(
            s for s in coordinator.data.decoded_models if s.discovered.model_id == model
        )
        assert entity.native_value == snapshot.decoded.fixed[name].value
        assert entity.native_value == 123.45


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf")])
def test_invalid_efficiency_samples_are_omitted(invalid):
    coordinator, _, _ = _coordinator_with(*sources())
    entity = SolarEfficiencySensor(
        coordinator, "test", 1, "inverter_efficiency", "inverter"
    )
    entity.async_write_ha_state = Mock()
    publish_sources(coordinator, entity, sources(), 0, slow=True)
    entity.async_write_ha_state.reset_mock()
    broken = change(sources(), 160, "DCW", invalid, 0)
    publish_sources(coordinator, entity, broken, 30, slow=True)
    assert len(entity._window.samples) == 1
    assert entity.native_value == 96
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_efficiencies_cannot_change_storage_write_decisions():
    from dataclasses import asdict

    from custom_components.fronius_pv_manager.storage_control import PowerSettings
    from tests.test_storage_control import controller

    plans = []
    for value in (None, 0, 25, 99, 1000):
        coordinator, control = controller()
        sensors = [
            SolarEfficiencySensor(coordinator, "test", 1, key, role)
            for key, role in (
                ("inverter_efficiency", "inverter"),
                ("rectifier_efficiency", "inverter"),
                ("battery_lifetime_efficiency", "storage"),
            )
        ]
        for sensor in sensors:
            sensor._managed = True
            sensor._published_value = value
        control.settings["1"] = asdict(PowerSettings(600, 3000, 0, 6000))
        await control.async_change(1, mode="manual")
        plans.append(coordinator.control_transport.write_calls)
    assert plans[0] and all(plan == plans[0] for plan in plans)


@pytest.mark.asyncio
async def test_displayed_control_status_retained_without_control_availability(clock):
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.fronius_pv_manager.control_entity import (
        control_entity_sources,
    )
    from custom_components.fronius_pv_manager.models import EntityPlatform
    from custom_components.fronius_pv_manager.storage_status import StorageControlStatus

    coordinator, transport, bases = runtime((124, 24))
    await coordinator.async_refresh()
    source = next(
        s
        for s in control_entity_sources(coordinator, EntityPlatform.NUMBER)
        if s.register_name == "MinRsvPct"
    )
    status = StorageControlStatus(coordinator, "test", source, "control_status")
    status.async_write_ha_state = Mock()
    status._handle_coordinator_update()
    initial = status.native_value
    status.async_write_ha_state.reset_mock()
    transport.fail_reads = True
    clock.now = 5
    await coordinator.async_refresh()
    status._handle_coordinator_update()
    assert status.available and status.native_value == initial
    status.async_write_ha_state.assert_not_called()
    assert coordinator.write_authority(1) is None
    with pytest.raises(ServiceValidationError):
        coordinator.storage_control.snapshot(1)


@pytest.mark.asyncio
async def test_proven_serial_replacement_discards_prior_readings(clock):
    from tests.test_sensor import _string_words

    coordinator, transport, bases = runtime((1, 65), (103, 50))
    from custom_components.fronius_pv_manager.register_maps import MODEL_1

    serial = next(r for r in MODEL_1.registers if r.name == "SN")
    for index, word in enumerate(_string_words("old", serial.size)):
        transport.registers[bases[1] + serial.offset + index] = word
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    for index, word in enumerate(_string_words("new", serial.size)):
        transport.registers[bases[1] + serial.offset + index] = word
    read = transport.read_holding_registers

    def fail(address, count):
        if address == bases[103]:
            raise ModbusTransportError("replacement has no reading yet")
        return read(address, count)

    transport.read_holding_registers = fail
    clock.now = 30
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available and entity.native_value is None
    assert coordinator.measurement_epochs[1] == 1


@pytest.mark.asyncio
async def test_successfully_discovered_empty_chain_removes_old_measurements(clock):
    coordinator, transport, _ = runtime((103, 50))
    await coordinator.async_refresh()
    entity = raw_sensor(coordinator)
    registers, _ = model_chain()
    transport.registers = registers
    coordinator.discovered_models_by_device.clear()
    clock.now = 5
    await coordinator.async_refresh()
    entity._handle_coordinator_update()
    assert not entity.available and entity.native_value is None
    assert coordinator.measurement_epochs[1] == 1
    assert coordinator.write_authority(1) is None
