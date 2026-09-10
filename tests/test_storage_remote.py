"""Complete power-window API, ownership, watchdog, and read-first restart safety."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager import async_unload_entry
from custom_components.fronius_pv_manager import storage_control as module
from custom_components.fronius_pv_manager.storage_control import (
    PowerSettings,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_runtime import WriteSequenceError
from tests.control_entity_fakes import MODEL_BASE
from tests.runtime_fakes import FakeEntry
from tests.test_storage_coexistence import entities_for, hlc
from tests.test_storage_quantization import hardware

OWNER = "energy_manager.entry1"
PROFILE = PowerSettings(1000, 2000, 0, 10240)
NEXT = PowerSettings(3000, 4000, 0, 10240)
DISCHARGE = PowerSettings(0, 10240, 1000, 2000)
NEUTRAL = {"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}


class Clock:
    """Deterministic replacement for HA's one-shot async_call_later scheduler."""

    def __init__(self):
        self.now = 1000.0
        self.calls = []

    def schedule(self, hass, delay, action):
        timer = {"deadline": self.now + delay, "action": action, "cancelled": False}
        self.calls.append(timer)

        def cancel():
            timer["cancelled"] = True

        return cancel

    async def advance(self, seconds, control):
        self.now += seconds
        due = [
            timer
            for timer in self.calls
            if not timer["cancelled"] and timer["deadline"] <= self.now
        ]
        for timer in due:
            timer["cancelled"] = True
            timer["action"](None)
        if control._watchdog_tasks:
            await asyncio.gather(*tuple(control._watchdog_tasks))

    @property
    def active(self):
        return [timer for timer in self.calls if not timer["cancelled"]]


@pytest_asyncio.fixture
async def remote(monkeypatch):
    coordinator, control = hardware()
    clock = Clock()
    monkeypatch.setattr(control, "_now", lambda: clock.now)
    monkeypatch.setattr(module, "async_call_later", clock.schedule)
    yield coordinator, control, clock
    await control.async_shutdown()


@pytest.mark.asyncio
async def test_full_window_changes_min_and_max_atomically(remote):
    coordinator, control, _ = remote
    await control.async_set_power_window(1, PROFILE)
    coordinator.write_runtime.async_write_sequence = AsyncMock(
        wraps=coordinator.write_runtime.async_write_sequence
    )
    before = len(coordinator.control_transport.write_calls)
    refreshes = coordinator.refresh_requests
    await control.async_set_power_window(1, NEXT)
    assert control.values(1) == NEXT
    assert control.mode(1) == "manual"
    assert control.status(1) == "manual_hlc"
    coordinator.write_runtime.async_write_sequence.assert_awaited_once_with(
        1,
        [
            (124, "StorCtl_Mod", 0),
            (124, "InWRte", 100),
            (124, "OutWRte", 100),
            (124, "InWRte", 39.06),
            (124, "OutWRte", -29.30),
            (124, "StorCtl_Mod", 3),
        ],
    )
    assert len(coordinator.control_transport.write_calls) == before + 6
    assert coordinator.refresh_requests == refreshes + 1
    assert control.last_targets["1"] == {
        "StorCtl_Mod": 3,
        "InWRte": 39.06,
        "OutWRte": -29.30,
    }


INVALID = [
    PowerSettings(3000, 2000, 0, 10240),
    PowerSettings(1, 10240, 1, 10240),
    PowerSettings(0, 10241, 0, 10240),
    PowerSettings(-1, 2000, 0, 10240),
    PowerSettings(0.5, 2000, 0, 10240),
    PowerSettings(True, 2000, 0, 10240),
    PowerSettings(float("nan"), 2000, 0, 10240),
    PowerSettings(1000, 1000, 0, 10240),
    None,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", INVALID)
@pytest.mark.parametrize("use_remote", [False, True])
async def test_invalid_complete_state_fails_without_write_or_lease_renewal(
    remote, settings, use_remote
):
    coordinator, control, clock = remote
    if use_remote:
        await control.async_acquire_remote_control(1, OWNER)
    writes = list(coordinator.control_transport.write_calls)
    saved = deepcopy(control.settings)
    leases = dict(control._leases)
    clock.now += 10
    with pytest.raises(ServiceValidationError):
        if use_remote:
            await control.async_set_remote_power_window(1, OWNER, settings)
        else:
            await control.async_set_power_window(1, settings)
    assert coordinator.control_transport.write_calls == writes
    assert control.settings == saved
    assert control._leases == leases


@pytest.mark.asyncio
async def test_acquire_activate_owner_and_refuse_second_owner(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    assert control.mode(1) == "remote"
    assert control.remote_owner(1) == OWNER
    assert control.snapshot(1)["StorCtl_Mod"].raw == 3
    assert control.last_targets["1"]["OutWRte"] == -9.77
    assert control._leases[1].expires_at == clock.now + 90
    assert len(clock.active) == 1
    writes = list(coordinator.control_transport.write_calls)
    with pytest.raises(ServiceValidationError, match="live owner"):
        await control.async_acquire_remote_control(1, "other")
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["heartbeat", "update", "release"])
async def test_only_owner_can_use_remote_commands(remote, command):
    coordinator, control, _ = remote
    await control.async_acquire_remote_control(1, OWNER)
    writes = list(coordinator.control_transport.write_calls)
    previous = dict(control._leases)
    with pytest.raises(ServiceValidationError, match="does not own"):
        if command == "heartbeat":
            await control.async_remote_heartbeat(1, "other")
        elif command == "update":
            await control.async_set_remote_power_window(1, "other", PROFILE)
        else:
            await control.async_release_remote_control(1, "other")
    assert coordinator.control_transport.write_calls == writes
    assert control._leases == previous


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["automatic", "manual", "remote"])
async def test_select_displays_remote_but_cannot_change_owned_mode(remote, mode):
    coordinator, control, _ = remote
    entities = await entities_for(coordinator)
    await control.async_acquire_remote_control(1, OWNER)
    select = hlc(entities, "operating_mode")
    assert select.options == ["automatic", "manual", "remote"]
    assert select.current_option == "remote"
    writes = list(coordinator.control_transport.write_calls)
    with pytest.raises(
        ServiceValidationError, match="Remote storage control is active"
    ):
        await select.async_select_option(mode)
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
async def test_remote_cannot_be_selected_without_an_owner(remote):
    coordinator, control, _ = remote
    entities = await entities_for(coordinator)
    with pytest.raises(ServiceValidationError, match="programmatic ownership"):
        await hlc(entities, "operating_mode").async_select_option("remote")
    assert not coordinator.control_transport.write_calls
    assert control.mode(1) == "automatic"


@pytest.mark.asyncio
async def test_all_hlc_controls_locked_while_remote(remote):
    coordinator, control, _ = remote
    entities = await entities_for(coordinator)
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    writes = list(coordinator.control_transport.write_calls)
    for field in asdict(PROFILE):
        assert hlc(entities, field).native_value == getattr(PROFILE, field)
        with pytest.raises(
            ServiceValidationError, match="Remote storage control is active"
        ):
            await hlc(entities, field).async_set_native_value(500)
    with pytest.raises(ServiceValidationError):
        await control.async_set_power_window(1, NEXT)
    assert coordinator.control_transport.write_calls == writes
    with pytest.raises(ServiceValidationError, match="Remote storage control"):
        await hlc(entities, "minimum_reserve").async_set_native_value(25)
    with pytest.raises(ServiceValidationError, match="Remote storage control"):
        await hlc(entities, "grid_charging_allowed").async_turn_on()
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("renew", ["heartbeat", "command"])
async def test_renew_postpones_watchdog_and_expiry_restores_automatic(
    remote, renew, caplog
):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    writes = len(coordinator.control_transport.write_calls)
    await clock.advance(80, control)
    if renew == "heartbeat":
        await control.async_remote_heartbeat(1, OWNER)
        assert len(coordinator.control_transport.write_calls) == writes
    else:
        await control.async_set_remote_power_window(1, OWNER, NEXT)
        assert control.values(1) == NEXT
        assert len(coordinator.control_transport.write_calls) == writes + 6
    deadline = control._leases[1].expires_at
    assert deadline == clock.now + 90
    before = len(coordinator.control_transport.write_calls)
    await clock.advance(89, control)
    assert control.mode(1) == "remote"
    assert len(coordinator.control_transport.write_calls) == before
    await clock.advance(1, control)
    assert control.mode(1) == "automatic"
    assert control.remote_owner(1) is None
    assert not control._leases
    assert not clock.active
    assert control.last_targets["1"] == NEUTRAL
    assert control.status(1) == "automatic"
    assert len(coordinator.control_transport.write_calls) == before + 3
    assert "automatic mode restored" in caplog.text
    await clock.advance(900, control)
    assert len(coordinator.control_transport.write_calls) == before + 3


@pytest.mark.asyncio
async def test_expired_owner_cannot_renew_or_command_even_before_timer_dispatch(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    clock.now += 90
    writes = list(coordinator.control_transport.write_calls)
    assert control.remote_owner(1) is None
    with pytest.raises(ServiceValidationError, match="expired"):
        await control.async_remote_heartbeat(1, OWNER)
    with pytest.raises(ServiceValidationError, match="expired"):
        await control.async_set_remote_power_window(1, OWNER, NEXT)
    assert coordinator.control_transport.write_calls == writes
    await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"


@pytest.mark.asyncio
async def test_release_writes_neutral_then_clears_ownership(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    start = len(coordinator.control_transport.write_calls)
    await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.last_targets["1"] == NEUTRAL
    assert control.values(1) == PROFILE
    assert not control._leases and not clock.active
    assert [
        address - MODEL_BASE
        for address, _ in coordinator.control_transport.write_calls[start:]
    ] == [3, 11, 10]
    await control.async_change(1, mode="manual")
    assert control.mode(1) == "manual"


@pytest.mark.asyncio
async def test_llc_override_heartbeat_observes_only_next_command_reapplies(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    await coordinator.write_runtime.async_write(1, 124, "InWRte", 50)
    assert control.status(1) == "low_level_override"
    # An external register change is likewise only observed by polling.
    coordinator.control_transport.registers[MODEL_BASE + 11] = 2500
    coordinator._poll_devices = coordinator._snapshot
    writes = list(coordinator.control_transport.write_calls)
    await coordinator.async_refresh()
    await clock.advance(30, control)
    await control.async_remote_heartbeat(1, OWNER)
    assert coordinator.control_transport.write_calls == writes
    assert control.status(1) == "low_level_override"
    await control.async_set_remote_power_window(1, OWNER, PROFILE)
    assert control.status(1) == "manual_hlc"
    assert control.mode(1) == "remote"
    assert len(coordinator.control_transport.write_calls) == len(writes) + 6


@pytest.mark.asyncio
async def test_remote_metadata_not_restored_as_authority_or_blind_write(remote):
    coordinator, control, _ = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    document = json.loads(open(control.store.path).read())["data"]
    assert document["modes"]["1"] == "remote"
    assert set(document) == {"settings", "modes", "last_targets"}
    assert OWNER not in json.dumps(document)
    fresh_coordinator, restored = hardware()
    restored.store = module.Store(fresh_coordinator.hass, 1, control.store.key)
    # Use the same persisted file, but no confirmed hardware at startup.
    fresh_coordinator.hass.config = coordinator.hass.config
    fresh_coordinator.data = module.replace(fresh_coordinator.data, devices=())
    await restored.async_load()
    assert restored.mode(1) == "automatic"
    assert restored.remote_owner(1) is None
    assert restored.status(1) == "unknown"
    assert not restored._watchdogs
    assert not fresh_coordinator.control_transport.write_calls
    fresh_coordinator.control_transport.registers.update(
        coordinator.control_transport.registers
    )
    await fresh_coordinator.async_request_refresh()
    assert restored.status(1) == "manual_hlc"
    assert restored.mode(1) == "automatic"
    assert not fresh_coordinator.control_transport.write_calls
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_config_entry_unload_cancels_watchdog_and_rejects_late_calls(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    writes = list(coordinator.control_transport.write_calls)
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    assert await async_unload_entry(coordinator.hass, entry)
    assert not clock.active and not control._leases
    await clock.advance(200, control)
    assert coordinator.control_transport.write_calls == writes
    with pytest.raises(ServiceValidationError, match="unloading"):
        await control.async_acquire_remote_control(1, OWNER)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["acquire", "update", "release", "watchdog"])
async def test_partial_failure_keeps_previous_target_and_ownership(
    remote, operation, caplog
):
    coordinator, control, clock = remote
    if operation != "acquire":
        await control.async_acquire_remote_control(1, OWNER, PROFILE)
    old_settings, old_targets = (
        deepcopy(control.settings),
        deepcopy(control.last_targets),
    )
    old_leases = dict(control._leases)
    original = coordinator.control_transport.write_holding_registers
    attempts = 0

    def write(address, words):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ModbusTransportError("uncertain write")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    if operation == "watchdog":
        await clock.advance(90, control)
        assert "automatic fallback failed" in caplog.text
        assert control.remote_owner(1) is None
        assert not clock.active
        await clock.advance(90, control)
    else:
        with pytest.raises(WriteSequenceError) as error:
            if operation == "acquire":
                await control.async_acquire_remote_control(1, OWNER, PROFILE)
            elif operation == "update":
                await control.async_set_remote_power_window(1, OWNER, NEXT)
            else:
                await control.async_release_remote_control(1, OWNER)
        assert len(error.value.completed) == 1
    assert attempts == 2  # No rollback or repeated watchdog writes.
    assert control.settings == old_settings
    assert control.last_targets == old_targets
    assert control._leases == old_leases
    if operation != "acquire":
        coordinator.control_transport.write_holding_registers = original
        await control.async_release_remote_control(1, OWNER)
        assert not control._leases
        assert control.mode(1) == "automatic"


@pytest.mark.asyncio
async def test_competing_acquisitions_and_llc_are_serialized(remote):
    coordinator, control, _ = remote
    entered, resume = asyncio.Event(), asyncio.Event()
    original = coordinator.hass.async_add_executor_job

    async def executor(fn, *args):
        if fn.__name__ == "_sequence_once":
            entered.set()
            await resume.wait()
        return await original(fn, *args)

    coordinator.hass.async_add_executor_job = executor
    first = asyncio.create_task(control.async_acquire_remote_control(1, OWNER, PROFILE))
    await entered.wait()
    second = asyncio.create_task(control.async_acquire_remote_control(1, "other", NEXT))
    llc = asyncio.create_task(
        coordinator.write_runtime.async_write(1, 124, "InWRte", 50)
    )
    await asyncio.sleep(0)
    assert not second.done() and not llc.done()
    resume.set()
    results = await asyncio.gather(first, second, llc, return_exceptions=True)
    assert results[0] is None
    assert isinstance(results[1], ServiceValidationError)
    assert not isinstance(results[2], Exception)
    assert control.remote_owner(1) == OWNER
    assert [
        address - MODEL_BASE for address, _ in coordinator.control_transport.write_calls
    ] == [
        3,
        11,
        10,
        11,
        10,
        3,
        11,
    ]
    assert control.status(1) == "low_level_override"


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_command_without_rescheduling(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    entered, resume = asyncio.Event(), asyncio.Event()
    original = coordinator.hass.async_add_executor_job

    async def executor(fn, *args):
        if fn.__name__ == "_sequence_once":
            entered.set()
            await resume.wait()
        return await original(fn, *args)

    coordinator.hass.async_add_executor_job = executor
    update = asyncio.create_task(control.async_set_remote_power_window(1, OWNER, NEXT))
    await entered.wait()
    shutdown = asyncio.create_task(control.async_shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not clock.active
    resume.set()
    await asyncio.gather(update, shutdown)
    assert not control._leases and not clock.active
    assert not control._watchdog_tasks


@pytest.mark.asyncio
async def test_stale_watchdog_callback_cannot_expire_a_renewed_lease(remote):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    old_callback = clock.active[0]["action"]
    await clock.advance(80, control)
    await control.async_remote_heartbeat(1, OWNER)
    writes = list(coordinator.control_transport.write_calls)
    clock.now += 10
    old_callback(None)  # Already dispatched just as the heartbeat cancelled it.
    await asyncio.gather(*tuple(control._watchdog_tasks))
    assert control.mode(1) == "remote"
    assert control.remote_owner(1) == OWNER
    assert len(clock.active) == 1
    assert clock.active[0]["deadline"] == clock.now + 80
    assert coordinator.control_transport.write_calls == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["manual", "new_owner"])
async def test_expired_failed_release_must_complete_before_new_control(
    remote, recovery
):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    original = coordinator.control_transport.write_holding_registers

    def fail(address, words):
        raise ModbusTransportError("device offline")

    coordinator.control_transport.write_holding_registers = fail
    await clock.advance(90, control)
    assert control.mode(1) == "remote"
    coordinator.control_transport.write_holding_registers = original
    start = len(coordinator.control_transport.write_calls)
    if recovery == "manual":
        await control.async_change(1, mode="manual")
        assert control.remote_owner(1) is None
    else:
        await control.async_acquire_remote_control(1, "new_owner")
        assert control.remote_owner(1) == "new_owner"
    assert [
        address - MODEL_BASE
        for address, _ in coordinator.control_transport.write_calls[start:]
    ] == [3, 11, 10, 3, 11, 10, 11, 10, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,value,register",
    [
        ("async_set_remote_minimum_reserve", 25, "MinRsvPct"),
        ("async_set_remote_grid_charging_allowed", True, "ChaGriSet"),
    ],
)
async def test_remote_policy_settings_owner_validation_renewal_and_failure(
    remote, method, value, register
):
    coordinator, control, clock = remote
    command = getattr(control, method)
    with pytest.raises(ServiceValidationError):
        await command(1, OWNER, value)
    assert not coordinator.control_transport.write_calls
    await control.async_acquire_remote_control(1, OWNER)
    before = list(coordinator.control_transport.write_calls)
    lease = control._leases[1]
    clock.now += 10
    with pytest.raises(ServiceValidationError):
        await command(1, "wrong", value)
    assert coordinator.control_transport.write_calls == before
    assert control._leases[1] == lease
    await command(1, OWNER, value)
    assert control._leases[1].expires_at == clock.now + 90
    observed = control.snapshot(1)[register]
    assert (observed.raw if register == "ChaGriSet" else observed.value) == value
    lease = control._leases[1]
    clock.now += 10
    coordinator.write_runtime.async_write = AsyncMock(
        side_effect=ModbusTransportError("failed")
    )
    with pytest.raises(ModbusTransportError):
        await command(1, OWNER, value)
    assert control._leases[1] == lease


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [4, 101, True, float("nan"), "25"])
async def test_invalid_remote_reserve_before_write(remote, value):
    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    before = list(coordinator.control_transport.write_calls)
    lease = control._leases[1]
    clock.now += 10
    with pytest.raises(ServiceValidationError):
        await control.async_set_remote_minimum_reserve(1, OWNER, value)
    assert coordinator.control_transport.write_calls == before
    assert control._leases[1] == lease


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,value",
    [
        ("async_set_remote_minimum_reserve", 25),
        ("async_set_remote_grid_charging_allowed", False),
    ],
)
async def test_remote_policy_settings_obey_write_policy(remote, method, value):
    from custom_components.fronius_pv_manager.write_runtime import WriteNotApprovedError

    coordinator, control, clock = remote
    await control.async_acquire_remote_control(1, OWNER)
    before = list(coordinator.control_transport.write_calls)
    lease = control._leases[1]
    coordinator.write_policies = {}
    clock.now += 10
    with pytest.raises(WriteNotApprovedError):
        await getattr(control, method)(1, OWNER, value)
    assert coordinator.control_transport.write_calls == before
    assert control._leases[1] == lease
