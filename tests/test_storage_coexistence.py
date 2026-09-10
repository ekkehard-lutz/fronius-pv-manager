"""HLC/LLC coexistence: observed state, persistent intent, and last write wins."""

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from custom_components.fronius_pv_manager import number, select, sensor, switch
from custom_components.fronius_pv_manager.storage_control import (
    PowerSettings,
    StorageControl,
    classify_storage_control,
)
from custom_components.fronius_pv_manager.storage_entity import StorageEntity
from custom_components.fronius_pv_manager.storage_status import StorageControlStatus
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_runtime import WriteSequenceError
from tests.control_entity_fakes import MODEL_BASE
from tests.runtime_fakes import FakeEntry
from tests.test_storage_control import controller


async def entities_for(coordinator):
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    for platform in (number, select, switch, sensor):
        await platform.async_setup_entry(coordinator.hass, entry, entities.extend)
    return entities


def hlc(entities, key):
    return next(
        entity
        for entity in entities
        if isinstance(entity, StorageEntity) and entity.key == key
    )


def llc(entities, register):
    return next(
        entity
        for entity in entities
        if isinstance(entity, (number.FroniusPVNumber, select.FroniusPVSelect))
        and entity._source.register_name == register
    )


@pytest.mark.asyncio
async def test_direct_hlc_controls_mirror_llc_and_external_confirmed_changes():
    coordinator, _ = controller()
    entities = await entities_for(coordinator)
    await llc(entities, "MinRsvPct").async_set_native_value(25)
    assert hlc(entities, "minimum_reserve").native_value == 25
    await llc(entities, "ChaGriSet").async_select_option("1")
    assert hlc(entities, "grid_charging_allowed").is_on is True
    # External writes become visible only after a confirmed poll.
    coordinator.control_transport.registers[MODEL_BASE + 5] = 3000
    coordinator.control_transport.registers[MODEL_BASE + 15] = 0
    assert hlc(entities, "minimum_reserve").native_value == 25
    await coordinator.async_request_refresh()
    assert hlc(entities, "minimum_reserve").native_value == 30
    assert hlc(entities, "grid_charging_allowed").is_on is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "register,value", [("InWRte", 10), ("OutWRte", 20), ("StorCtl_Mod", 1)]
)
async def test_llc_overrides_without_replacing_hlc_settings_or_mode(register, value):
    coordinator, control = controller()
    control.settings["1"] = asdict(PowerSettings(0, 3000, 0, 2400))
    await control.async_change(1, mode="manual")
    entities = await entities_for(coordinator)
    previous = deepcopy(control.settings)
    target = deepcopy(control.last_targets)
    entity = llc(entities, register)
    if register == "StorCtl_Mod":
        await entity.async_select_option(str(value))
    else:
        await entity.async_set_native_value(value)
    assert control.settings == previous
    assert control.last_targets == target
    assert hlc(entities, "operating_mode").current_option == "manual"
    assert hlc(entities, "control_status").native_value == "low_level_override"
    writes = list(coordinator.control_transport.write_calls)
    # Exercise normal coordinator publication and entity discovery callbacks.
    coordinator._poll_devices = coordinator._snapshot
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_transport.write_calls == writes
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert restored.settings == previous
    assert restored.mode(1) == "manual"
    assert restored.status(1) == "low_level_override"


@pytest.mark.asyncio
async def test_manual_mode_persists_when_llc_releases_control():
    coordinator, control = controller()
    await control.async_change(1, mode="manual")
    await coordinator.write_runtime.async_write(1, 124, "StorCtl_Mod", 0)
    assert control.mode(1) == "manual"
    # A neutral automatic window has priority over the previous manual target.
    assert control.status(1) == "automatic"
    await coordinator.write_runtime.async_write(1, 124, "InWRte", 50)
    assert control.status(1) == "low_level_override"
    assert control.mode(1) == "manual"


@pytest.mark.asyncio
async def test_override_after_automatic_does_not_activate_persisted_manual_settings():
    coordinator, control = controller()
    await control.async_change(1, mode="automatic")
    assert control.status(1) == "automatic"
    await coordinator.write_runtime.async_write(1, 124, "StorCtl_Mod", 3)
    assert control.status(1) == "low_level_override"
    assert control.mode(1) == "automatic"
    count = len(coordinator.control_transport.write_calls)
    await control.async_change(1, field="maximum_charge_power", value=3000)
    assert len(coordinator.control_transport.write_calls) == count
    assert control.status(1) == "low_level_override"
    assert control.last_targets["1"] == {
        "StorCtl_Mod": 0,
        "InWRte": 100,
        "OutWRte": 100,
    }
    # Explicit automatic selection restores every neutral register.
    await control.async_change(1, mode="automatic")
    assert control.status(1) == "automatic"
    assert control.values(1).maximum_charge_power == 3000


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "reselect"])
async def test_explicit_manual_action_reapplies_complete_window(action):
    coordinator, control = controller()
    await control.async_change(1, field="maximum_discharge_power", value=2400)
    await control.async_change(1, mode="manual")
    await coordinator.write_runtime.async_write(1, 124, "InWRte", 20)
    await coordinator.write_runtime.async_write(1, 124, "StorCtl_Mod", 0)
    assert control.status(1) == "low_level_override"
    entities = await entities_for(coordinator)
    start = len(coordinator.control_transport.write_calls)
    if action == "edit":
        await hlc(entities, "maximum_charge_power").async_set_native_value(3000)
    else:
        await hlc(entities, "operating_mode").async_select_option("manual")
    assert [
        address - MODEL_BASE
        for address, _ in coordinator.control_transport.write_calls[start:]
    ] == [3, 11, 10, 11, 10, 3]
    assert control.status(1) == "manual_hlc"
    assert control.last_targets["1"] == {
        "StorCtl_Mod": 3,
        "InWRte": 50 if action == "edit" else 100,
        "OutWRte": 40,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_mode", ["manual", "automatic"])
async def test_failed_sequence_preserves_last_success_and_next_poll_classifies(
    initial_mode,
):
    coordinator, control = controller()
    await control.async_change(1, mode=initial_mode)
    previous_target = deepcopy(control.last_targets)
    previous_settings = deepcopy(control.settings)
    original = coordinator.control_transport.write_holding_registers
    attempts = 0

    def write(address, words):
        nonlocal attempts
        attempts += 1
        if attempts == 5:
            raise ModbusTransportError("uncertain write")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    with pytest.raises(WriteSequenceError) as failure:
        await control.async_change(
            1, mode="manual", field="maximum_charge_power", value=3000
        )
    assert len(failure.value.completed) == 4
    assert control.last_targets == previous_target
    assert control.settings == previous_settings
    assert control.mode(1) == initial_mode
    await coordinator.async_request_refresh()
    assert control.status(1) == "low_level_override"
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert restored.last_targets == previous_target
    assert restored.mode(1) == initial_mode


@pytest.mark.asyncio
async def test_llc_queued_during_hlc_sequence_runs_after_it_and_wins():
    coordinator, control = controller()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = coordinator.hass.async_add_executor_job

    async def executor(fn, *args):
        if fn.__name__ == "_sequence_once":
            entered.set()
            await resume.wait()
        return await original(fn, *args)

    coordinator.hass.async_add_executor_job = executor
    high = asyncio.create_task(control.async_change(1, mode="manual"))
    await entered.wait()
    low = asyncio.create_task(
        coordinator.write_runtime.async_write(1, 124, "InWRte", 20)
    )
    await asyncio.sleep(0)
    assert not low.done()
    resume.set()
    await asyncio.gather(high, low)
    assert [
        address - MODEL_BASE for address, _ in coordinator.control_transport.write_calls
    ] == [3, 11, 10, 11, 10, 3, 11]
    assert control.mode(1) == "manual"
    assert control.last_targets["1"]["InWRte"] == 100
    assert control.snapshot(1)["InWRte"].value == 20
    assert control.status(1) == "low_level_override"


@pytest.mark.parametrize(
    "current,expected",
    [
        ({"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}, "automatic"),
        ({"StorCtl_Mod": 3, "InWRte": 50, "OutWRte": 40}, "manual_hlc"),
        ({"StorCtl_Mod": 1, "InWRte": -20, "OutWRte": -30}, "low_level_override"),
        ({"StorCtl_Mod": 2, "InWRte": -20, "OutWRte": -30}, "low_level_override"),
        ({"StorCtl_Mod": 3, "InWRte": -20, "OutWRte": -30}, "unknown"),
        ({"StorCtl_Mod": 4, "InWRte": 100, "OutWRte": 100}, "unknown"),
        ({"StorCtl_Mod": 0, "InWRte": None, "OutWRte": 100}, "unknown"),
        ({"StorCtl_Mod": 0, "InWRte": float("nan"), "OutWRte": 100}, "unknown"),
        ({"StorCtl_Mod": 0, "InWRte": 101, "OutWRte": 100}, "unknown"),
        ({"StorCtl_Mod": True, "InWRte": 100, "OutWRte": 100}, "unknown"),
        ({"StorCtl_Mod": 0, "InWRte": 100}, "unknown"),
        (None, "unknown"),
    ],
)
def test_status_uses_only_valid_complete_documented_state(current, expected):
    assert (
        classify_storage_control(
            current,
            {
                "StorCtl_Mod": 3,
                "InWRte": 50,
                "OutWRte": 40,
            },
        )
        == expected
    )


@pytest.mark.asyncio
async def test_no_previous_target_is_not_assumed_from_mode_or_watts():
    coordinator, control = controller()
    assert control.status(1) == "unknown"
    # Initial HLC selection is automatic even if an expert already enabled limits.
    coordinator.control_transport.registers[MODEL_BASE + 3] = 3
    coordinator.data = coordinator._snapshot()
    assert control.mode(1) == "automatic"
    assert control.status(1) == "unknown"
    assert not coordinator.control_transport.write_calls


@pytest.mark.asyncio
async def test_restart_uses_saved_target_not_rederived_watts_and_never_writes():
    coordinator, control = controller()
    await control.async_change(1, field="maximum_charge_power", value=3000)
    await control.async_change(1, mode="manual")
    # Changing WChaMax changes how watts would map to percentages, not the
    # historical register target that was actually applied.
    coordinator.control_transport.registers[MODEL_BASE] = 12000
    writes = list(coordinator.control_transport.write_calls)
    restored = StorageControl(coordinator)
    await restored.async_load()
    saved_data = coordinator.data
    coordinator.data = replace(saved_data, devices=())
    assert restored.status(1) == "unknown"
    await coordinator.async_request_refresh()
    assert restored.status(1) == "manual_hlc"
    assert restored.mode(1) == "manual"
    assert restored.values(1).maximum_charge_power == 3000
    assert restored.last_targets["1"]["InWRte"] == 50
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
async def test_watt_only_development_storage_loads_without_inventing_target():
    coordinator, control = controller()
    saved = {"1": asdict(PowerSettings(0, 3000, 0, 2400))}
    await control.store.async_save(saved)
    await control.async_load()
    assert control.settings == saved
    assert control.mode(1) == "automatic"
    assert not control.last_targets
    assert not coordinator.control_transport.write_calls


@pytest.mark.asyncio
async def test_status_entity_has_stable_id_localized_enum_and_no_write_path():
    import json

    from custom_components.fronius_pv_manager.write_policy_loader import (
        DEFAULT_POLICY_PATH,
    )

    coordinator, control = controller()
    entities = await entities_for(coordinator)
    entity = hlc(entities, "control_status")
    assert isinstance(entity, StorageControlStatus)
    assert entity.entity_registry_enabled_default
    assert entity.suggested_object_id == "storage_control_status"
    assert entity.unique_id == "test-entry_device1_storage_hlc_control_status"
    assert entity.native_value == "unknown"
    await control.async_change(1, mode="manual")
    assert entity.native_value == "manual_hlc"
    for path in ("strings.json", "translations/en.json", "translations/de.json"):
        item = json.loads((DEFAULT_POLICY_PATH.parent / path).read_text())["entity"][
            "sensor"
        ]["control_status"]
        assert item["name"]
        assert set(item["state"]) == set(entity.options)


@pytest.mark.asyncio
async def test_external_rate_write_is_observed_without_enforcement():
    coordinator, control = controller()
    await control.async_change(1, mode="manual")
    previous = deepcopy(control.settings)
    writes = list(coordinator.control_transport.write_calls)
    coordinator.control_transport.registers[MODEL_BASE + 10] = 1500
    assert control.status(1) == "manual_hlc"
    coordinator._poll_devices = coordinator._snapshot
    await coordinator.async_refresh()
    assert control.status(1) == "low_level_override"
    assert control.settings == previous
    assert control.mode(1) == "manual"
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
async def test_unavailable_snapshot_cannot_report_previous_manual_profile_active():
    coordinator, control = controller()
    await control.async_change(1, mode="manual")
    coordinator.last_update_success = False
    assert control.status(1) == "unknown"
    coordinator.last_update_success = True
    device = coordinator.data.devices[0]
    coordinator.data = replace(
        coordinator.data, devices=(replace(device, available=False),)
    )
    assert control.status(1) == "unknown"


@pytest.mark.asyncio
async def test_first_failed_hlc_sequence_does_not_create_remembered_target():
    coordinator, control = controller()
    original = coordinator.control_transport.write_holding_registers
    attempts = 0

    def write(address, words):
        nonlocal attempts
        attempts += 1
        if attempts == 6:
            # The write may have applied, but without verification no target is known.
            original(address, words)
            raise ModbusTransportError("response lost")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    with pytest.raises(WriteSequenceError):
        await control.async_change(1, mode="manual")
    assert not control.last_targets
    await coordinator.async_request_refresh()
    assert control.status(1) == "unknown"
    assert control.mode(1) == "automatic"
