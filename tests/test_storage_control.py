"""High-level power windows, persistent settings and verified write sequences."""

import asyncio
import json
from dataclasses import asdict

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager import number, select, switch
from custom_components.fronius_pv_manager.storage_control import (
    PowerSettings,
    StorageControl,
    percent_to_watts,
    power_window,
    watts_to_percent,
)
from custom_components.fronius_pv_manager.storage_entity import StorageEntity
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_policy_loader import (
    DEFAULT_POLICY_PATH,
    load_write_policy_text,
)
from custom_components.fronius_pv_manager.write_runtime import (
    WriteInvalidValueError,
    WriteNotApprovedError,
    WriteSequenceError,
)
from tests.control_entity_fakes import MODEL_BASE, ControlCoordinator
from tests.runtime_fakes import FakeEntry


def controller():
    coordinator = ControlCoordinator(
        load_write_policy_text(DEFAULT_POLICY_PATH.read_text())
    )
    coordinator.control_transport.registers[MODEL_BASE] = 6000
    coordinator.control_transport.registers[MODEL_BASE + 23] = 0xFFFE
    coordinator.data = coordinator._snapshot()
    return coordinator, coordinator.storage_control


@pytest.mark.parametrize("reference", [5000, 6000, 10000])
@pytest.mark.parametrize("fraction", [0, 0.1, 0.5, 1])
def test_conversion_boundaries(reference, fraction):
    watts = reference * fraction
    percent = watts_to_percent(watts, reference)
    assert percent == fraction * 100
    assert percent_to_watts(percent, reference) == watts


@pytest.mark.parametrize("reference", [0, -1, None, float("nan"), float("inf")])
def test_unavailable_reference(reference):
    with pytest.raises(ServiceValidationError):
        watts_to_percent(0, reference)


@pytest.mark.parametrize("watts", [-1, 6001, float("nan"), float("inf"), True])
def test_invalid_power(watts):
    with pytest.raises(ServiceValidationError):
        watts_to_percent(watts, 6000)


@pytest.mark.parametrize(
    "settings,expected",
    [
        (PowerSettings(0, 6000, 0, 6000), (100, 100)),
        (PowerSettings(0, 0, 0, 0), (0, 0)),
        (PowerSettings(600, 3000, 0, 6000), (50, -10)),
        (PowerSettings(0, 6000, 600, 3000), (-10, 50)),
        (PowerSettings(6000, 6000, 0, 0), (100, -100)),
        (PowerSettings(0, 0, 6000, 6000), (-100, 100)),
    ],
)
def test_manual_windows(settings, expected):
    target = power_window(settings, 6000, "manual", -2)
    assert target == dict(StorCtl_Mod=3, InWRte=expected[0], OutWRte=expected[1])
    assert -target["InWRte"] <= target["OutWRte"]


def test_automatic_target():
    assert power_window(PowerSettings(600, 3000, 0, 2000), 6000, "automatic") == {
        "StorCtl_Mod": 0,
        "InWRte": 100,
        "OutWRte": 100,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        PowerSettings(100, 6000, 100, 6000),
        PowerSettings(6001, 6000, 0, 6000),
        PowerSettings(0, 6000, 100, 0),
    ],
)
async def test_invalid_window_rejected_before_io(settings):
    coordinator, control = controller()
    control.settings["1"] = asdict(settings)
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, mode="manual")
    assert not coordinator.control_transport.write_calls
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_persistence_survives_automatic_and_restart():
    coordinator, control = controller()
    await control.async_load()
    await control.async_change(1, field="maximum_charge_power", value=3000)
    assert not coordinator.control_transport.write_calls
    await control.async_change(1, field="minimum_charge_power", value=600)
    await control.async_change(1, mode="manual")
    assert control.mode(1) == "manual"
    await control.async_change(1, mode="automatic")
    assert control.mode(1) == "automatic"
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert restored.values(1) == PowerSettings(600, 3000, 0, 6000)
    assert (
        coordinator.data.devices[0].decoded_models[0].decoded.fixed["InWRte"].value
        == 100
    )
    await restored.async_change(1, mode="manual")
    fixed = control.snapshot(1)
    assert fixed["InWRte"].value == 50
    assert fixed["OutWRte"].value == -10
    assert coordinator.refresh_requests == 3


@pytest.mark.asyncio
async def test_sequence_preflights_policy_and_encoding_before_first_write():
    coordinator, control = controller()
    sequence = [(124, "StorCtl_Mod", 0), (124, "MinRsvPct", 4)]
    with pytest.raises(WriteInvalidValueError):
        await coordinator.write_runtime.async_write_sequence(1, sequence)
    with pytest.raises(WriteNotApprovedError):
        await coordinator.write_runtime.async_write_sequence(1, [(124, "VAChaMax", 1)])
    # Low-level callers still must supply exactly encodable percentage values.
    with pytest.raises(WriteInvalidValueError):
        await coordinator.write_runtime.async_write_sequence(
            1, [(124, "StorCtl_Mod", 0), (124, "InWRte", 0.001)]
        )
    assert not coordinator.control_transport.write_calls
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_sequence_lock_readbacks_order_and_single_refresh():
    coordinator, control = controller()
    original = coordinator.control_transport.write_holding_registers
    observed = []

    def write(address, words):
        assert coordinator.io_lock.locked()
        assert coordinator.refresh_requests == 0
        original(address, words)
        observed.append(address - MODEL_BASE)

    coordinator.control_transport.write_holding_registers = write
    await control.async_change(1, mode="manual")
    assert observed == [3, 11, 10, 11, 10, 3]
    assert coordinator.refresh_requests == 1
    assert not coordinator.io_lock.locked()
    # Two preparation reads plus two mandatory readbacks for each repeated register.
    for offset in (3, 10, 11):
        assert (
            coordinator.control_transport.read_calls.count((MODEL_BASE + offset, 1))
            == 4
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_step", range(1, 7))
async def test_partial_failure_reports_progress_without_refresh_or_rollback(
    failure_step,
):
    coordinator, control = controller()
    original = coordinator.control_transport.write_holding_registers
    attempts = []

    def write(address, words):
        attempts.append(address)
        if len(attempts) == failure_step:
            raise ModbusTransportError("uncertain write")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    with pytest.raises(WriteSequenceError, match="no rollback") as error:
        await control.async_change(1, mode="manual")
    assert len(error.value.completed) == failure_step - 1
    assert len(attempts) == failure_step
    assert coordinator.refresh_requests == 0
    assert control.settings == {}


@pytest.mark.asyncio
async def test_sequence_excludes_competing_poll_and_write():
    coordinator, _ = controller()
    entered = asyncio.Event()
    resume = asyncio.Event()
    original = coordinator.hass.async_add_executor_job

    async def executor(fn, *args):
        if fn.__name__ == "_sequence_once":
            entered.set()
            await resume.wait()
        return await original(fn, *args)

    coordinator.hass.async_add_executor_job = executor
    task = asyncio.create_task(
        coordinator.write_runtime.async_write_sequence(
            1, [(124, "StorCtl_Mod", 0), (124, "InWRte", 100)]
        )
    )
    await entered.wait()
    competitor = asyncio.create_task(
        coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)
    )
    coordinator._poll_devices = lambda: coordinator.data
    poll = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    assert not competitor.done() and not poll.done()
    assert not coordinator.control_transport.write_calls
    resume.set()
    await asyncio.gather(task, competitor, poll)
    assert [
        address - MODEL_BASE for address, _ in coordinator.control_transport.write_calls
    ] == [3, 11, 5]


@pytest.mark.asyncio
async def test_entities_localization_ranges_policy_and_verified_switch():
    coordinator, control = controller()
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    for platform in (number, select, switch):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    hlc = {
        entity.key: entity for entity in entities if isinstance(entity, StorageEntity)
    }
    assert len(hlc) == 7
    assert len({entity.unique_id for entity in entities}) == len(entities)
    assert all(entity.entity_registry_enabled_default for entity in hlc.values())
    assert all(
        not entity.entity_registry_enabled_default
        for entity in entities
        if not isinstance(entity, StorageEntity)
    )
    for key, entity in hlc.items():
        assert entity.available
        assert entity.suggested_object_id == f"storage_{key}"
        if key.endswith("power"):
            assert (entity.native_min_value, entity.native_max_value) == (0, 6000)
            assert entity.native_unit_of_measurement == "W"
    for value in (0, 4.99, 101):
        with pytest.raises(ServiceValidationError):
            await hlc["minimum_reserve"].async_set_native_value(value)
    assert not coordinator.control_transport.write_calls
    await hlc["minimum_reserve"].async_set_native_value(5)
    assert hlc["minimum_reserve"].native_value == 5
    grid = hlc["grid_charging_allowed"]
    await grid.async_turn_on()
    assert grid.is_on
    await grid.async_turn_off()
    assert not grid.is_on
    root = DEFAULT_POLICY_PATH.parent
    for filename in ("strings.json", "translations/en.json", "translations/de.json"):
        translated = json.loads((root / filename).read_text())["entity"]
        assert set(hlc) <= {key for platform in translated.values() for key in platform}
    coordinator.write_policies = {}
    with pytest.raises(WriteNotApprovedError):
        await grid.async_turn_on()


@pytest.mark.asyncio
async def test_entity_rejects_window_with_no_representable_point():
    coordinator, control = controller()
    control.settings["1"] = asdict(PowerSettings(1, 1, 0, 6000))
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await select.async_setup_entry(coordinator.hass, entry, entities.extend)
    entity = next(item for item in entities if isinstance(item, StorageEntity))
    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("manual")
    assert not coordinator.control_transport.write_calls


@pytest.mark.asyncio
async def test_reference_uses_decoded_scale_and_updates_entity_range():
    coordinator, control = controller()
    coordinator.control_transport.registers[MODEL_BASE] = 800
    coordinator.control_transport.registers[MODEL_BASE + 16] = 1
    coordinator.data = coordinator._snapshot()
    assert control.reference(1) == 8000
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await number.async_setup_entry(coordinator.hass, entry, entities.extend)
    entity = next(
        item
        for item in entities
        if isinstance(item, StorageEntity) and item.key == "maximum_charge_power"
    )
    assert entity.native_max_value == 8000
    await entity.async_set_native_value(8000)
    await control.async_change(1, mode="manual")
    assert control.snapshot(1)["InWRte"].value == 100
    coordinator.control_transport.registers[MODEL_BASE] = 900
    coordinator.data = coordinator._snapshot()
    assert entity.native_max_value == 9000
    assert entity.native_value == 8000


@pytest.mark.asyncio
async def test_automatic_release_preserves_settings_after_reference_decreases():
    coordinator, control = controller()
    await control.async_change(1, mode="manual")
    coordinator.control_transport.registers[MODEL_BASE] = 5000
    coordinator.data = coordinator._snapshot()
    await control.async_change(1, mode="automatic")
    assert control.mode(1) == "automatic"
    assert control.values(1).maximum_charge_power == 6000
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, mode="manual")


@pytest.mark.asyncio
@pytest.mark.parametrize("readback_error", [True, False])
async def test_sequence_verification_failures_stop_without_refresh(readback_error):
    coordinator, _ = controller()
    transport = coordinator.control_transport
    original = transport.read_holding_registers

    def read(address, count):
        if transport.write_calls:
            if readback_error:
                raise ModbusTransportError("readback failed")
            return (99,)
        return original(address, count)

    transport.read_holding_registers = read
    with pytest.raises(WriteSequenceError) as error:
        await coordinator.write_runtime.async_write_sequence(
            1, [(124, "StorCtl_Mod", 0), (124, "InWRte", 100)]
        )
    assert error.value.failed_register == "StorCtl_Mod"
    assert not error.value.completed
    assert len(transport.write_calls) == 1
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_cancellation_does_not_release_lock_while_sequence_runs():
    coordinator, _ = controller()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = coordinator.hass.async_add_executor_job

    async def executor(fn, *args):
        if fn.__name__ == "_sequence_once":
            entered.set()
            await resume.wait()
        return await original(fn, *args)

    coordinator.hass.async_add_executor_job = executor
    task = asyncio.create_task(
        coordinator.write_runtime.async_write_sequence(
            1, [(124, "StorCtl_Mod", 0), (124, "InWRte", 100)]
        )
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert coordinator.io_lock.locked()
    resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(coordinator.control_transport.write_calls) == 2
    assert not coordinator.io_lock.locked()
