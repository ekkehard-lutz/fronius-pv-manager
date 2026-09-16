"""Temporary takeover restores user policy, always with automatic power control."""

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.storage_control import PowerSettings
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_runtime import WriteSequenceError
from tests.control_entity_fakes import MODEL_BASE
from tests.test_storage_remote import NEUTRAL, NEXT, OWNER, PROFILE
from tests.test_storage_remote import remote as remote_fixture

remote = remote_fixture
USER = PowerSettings(0, 5000, 0, 6000)


async def prepare(control, mode="manual"):
    await control.async_set_power_window(1, USER)
    if mode == "automatic":
        await control.async_change(1, mode="automatic")
    await control.async_write_policy_setting(1, "MinRsvPct", 20)
    await control.async_write_policy_setting(1, "ChaGriSet", 0)


async def takeover(control):
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    await control.async_set_remote_minimum_reserve(1, OWNER, 35)
    await control.async_set_remote_grid_charging_allowed(1, OWNER, True)


def saved(control):
    return json.loads(Path(control.store.path).read_text())["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["manual", "automatic"])
@pytest.mark.parametrize("expiry", [False, True])
async def test_restore_all_state_always_automatic(remote, mode, expiry):
    coordinator, control, clock = remote
    await prepare(control, mode)
    await takeover(control)
    snapshot = control._pre_remote[1]
    assert snapshot.minimum_reserve == 20
    assert snapshot.grid_charging_allowed is False
    assert snapshot.power_settings == USER
    await control.async_set_remote_power_window(1, OWNER, NEXT)
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    assert control._pre_remote[1] is snapshot
    assert saved(control)["settings"]["1"] == asdict(USER)
    assert "pre_remote" not in json.dumps(saved(control))
    start = len(coordinator.control_transport.write_calls)
    if expiry:
        await clock.advance(90, control)
    else:
        await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.last_targets["1"] == NEUTRAL
    assert control.status(1) == "automatic"
    assert control.snapshot(1)["MinRsvPct"].value == 20
    assert control.snapshot(1)["ChaGriSet"].raw == 0
    assert control.values(1) == USER
    assert saved(control)["settings"]["1"] == asdict(USER)
    assert saved(control)["modes"]["1"] == "automatic"
    assert not control._leases and not control._pre_remote and not control._cleanup
    assert not clock.active
    assert coordinator.control_transport.write_calls[start:] == [
        (MODEL_BASE + 3, (0,)),
        (MODEL_BASE + 11, (10000,)),
        (MODEL_BASE + 10, (10000,)),
        (MODEL_BASE + 5, (2000,)),
        (MODEL_BASE + 15, (0,)),
    ]
    await control.async_change(1, mode="manual")
    assert control.values(1) == USER
    assert control.mode(1) == "manual"
    assert control.last_targets["1"]["InWRte"] == 48.82


@pytest.mark.asyncio
async def test_second_lease_has_fresh_snapshot(remote):
    _, control, _ = remote
    await prepare(control)
    await takeover(control)
    await control.async_release_remote_control(1, OWNER)
    await control.async_set_power_window(1, NEXT)
    await control.async_write_policy_setting(1, "MinRsvPct", 40)
    await control.async_write_policy_setting(1, "ChaGriSet", 1)
    await takeover(control)
    assert control._pre_remote[1].power_settings == NEXT
    assert control._pre_remote[1].minimum_reserve == 40
    assert control._pre_remote[1].grid_charging_allowed is True
    await control.async_release_remote_control(1, OWNER)
    assert control.values(1) == NEXT
    assert control.snapshot(1)["MinRsvPct"].value == 40
    assert control.snapshot(1)["ChaGriSet"].raw == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["write", "persistence"])
async def test_failed_acquire_never_creates_or_replaces_snapshot(
    remote, existing, failure
):
    coordinator, control, _ = remote
    await prepare(control)
    if existing:
        await takeover(control)
    snapshots = dict(control._pre_remote)
    if failure == "write":

        def fail(address, words):
            raise ModbusTransportError("failed")

        coordinator.control_transport.write_holding_registers = fail
    else:
        control.store.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises((WriteSequenceError, OSError)):
        await control.async_acquire_remote_control(1, OWNER, NEXT)
    assert control._pre_remote == snapshots


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("expiry", [False, True])
async def test_partial_cleanup_retains_snapshot_and_no_retry_loop(remote, step, expiry):
    coordinator, control, clock = remote
    await prepare(control)
    await takeover(control)
    original = coordinator.control_transport.write_holding_registers
    attempts = 0

    def write(address, words):
        nonlocal attempts
        attempts += 1
        if attempts == step:
            raise ModbusTransportError("restore failed")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    start = len(coordinator.control_transport.write_calls)
    if expiry:
        await clock.advance(90, control)
    else:
        with pytest.raises(WriteSequenceError):
            await control.async_release_remote_control(1, OWNER)
    assert attempts == step
    assert control._cleanup[1].verified_steps == step - 1
    assert control._pre_remote[1].power_settings == USER
    assert control.values(1) == PROFILE
    assert control.remote_owner(1) is None
    assert not clock.active
    if step > 3:
        assert control.mode(1) == "automatic"
        assert control.last_targets["1"] == NEUTRAL
    else:
        assert control.mode(1) == "remote"
        assert control.last_targets["1"] != NEUTRAL
    assert all(
        words == (0,)
        for address, words in coordinator.control_transport.write_calls[start:]
        if address == MODEL_BASE + 3
    )
    with pytest.raises(ServiceValidationError, match="cleanup"):
        await control.async_remote_heartbeat(1, OWNER)
    with pytest.raises(ServiceValidationError, match="cleanup"):
        await control.async_set_remote_power_window(1, OWNER, NEXT)
    await clock.advance(900, control)
    assert attempts == step
    coordinator.control_transport.write_holding_registers = original
    await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.values(1) == USER
    assert not control._pre_remote and not control._cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("register,verified", [("MinRsvPct", 3), ("ChaGriSet", 4)])
async def test_policy_preflight_failure_still_prioritizes_automatic(
    remote, register, verified
):
    coordinator, control, _ = remote
    await prepare(control)
    await takeover(control)
    policies = coordinator.write_policies
    coordinator.write_policies = {
        key: value for key, value in policies.items() if key != (124, register)
    }
    before = len(coordinator.control_transport.write_calls)
    with pytest.raises(WriteSequenceError, match="preflight"):
        await control.async_release_remote_control(1, OWNER)
    assert len(coordinator.control_transport.write_calls) == before + verified
    assert control.mode(1) == "automatic"
    assert control.last_targets["1"] == NEUTRAL
    assert control._cleanup[1].failed_step == register
    assert control._pre_remote[1].power_settings == USER


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [False, True])
async def test_persistence_failure_retains_verified_automatic_and_pending_profile(
    remote, expiry
):
    _, control, clock = remote
    await prepare(control)
    await takeover(control)
    save = control.store.async_save
    control.store.async_save = AsyncMock(side_effect=OSError("disk full"))
    if expiry:
        await clock.advance(90, control)
    else:
        with pytest.raises(OSError, match="disk full"):
            await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.last_targets["1"] == NEUTRAL
    assert control.snapshot(1)["MinRsvPct"].value == 20
    assert control.snapshot(1)["ChaGriSet"].raw == 0
    assert control.values(1) == PROFILE
    assert control._cleanup[1].verified_steps == 5
    assert control._cleanup[1].failed_step == "persistence"
    assert control._pre_remote[1].power_settings == USER
    assert not clock.active
    control.store.async_save = save
    await control.async_release_remote_control(1, OWNER)
    assert control.values(1) == USER


@pytest.mark.asyncio
async def test_snapshot_uses_live_registers_immediately_before_takeover(remote):
    coordinator, control, _ = remote
    await prepare(control)
    # Change device state without a poll: capture must not use the stale cache.
    coordinator.control_transport.registers[MODEL_BASE + 5] = 3000
    coordinator.control_transport.registers[MODEL_BASE + 15] = 1
    assert control.snapshot(1)["MinRsvPct"].value == 20
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    assert control._pre_remote[1].minimum_reserve == 30
    assert control._pre_remote[1].grid_charging_allowed is True


@pytest.mark.asyncio
async def test_snapshot_read_failure_prevents_any_takeover_write(remote):
    coordinator, control, _ = remote
    await prepare(control)
    coordinator.control_transport.registers[MODEL_BASE + 15] = 99
    before = list(coordinator.control_transport.write_calls)
    with pytest.raises(ServiceValidationError):
        await control.async_acquire_remote_control(1, OWNER, PROFILE)
    assert not control._pre_remote and not control._leases
    assert coordinator.control_transport.write_calls == before


@pytest.mark.asyncio
async def test_release_restores_profile_even_if_reference_has_decreased(remote):
    coordinator, control, _ = remote
    await prepare(control)
    await takeover(control)
    coordinator.control_transport.registers[MODEL_BASE] = 4000
    await coordinator.async_request_refresh()
    await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.values(1) == USER
    assert not control._pre_remote


@pytest.mark.asyncio
async def test_refresh_failure_after_cleanup_retains_verified_progress(remote):
    coordinator, control, _ = remote
    await prepare(control)
    await takeover(control)
    coordinator.async_request_refresh = AsyncMock(side_effect=RuntimeError("refresh"))
    with pytest.raises(WriteSequenceError, match="refresh"):
        await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control.last_targets["1"] == NEUTRAL
    assert control._cleanup[1].verified_steps == 5
    assert control._cleanup[1].failed_step == "refresh"
    assert control.values(1) == PROFILE
    assert control._pre_remote[1].power_settings == USER


@pytest.mark.asyncio
async def test_persistence_keeps_other_devices_original_profiles(remote):
    from custom_components.fronius_pv_manager.storage_control import PreRemoteSnapshot

    _, control, _ = remote
    await prepare(control)
    # A second remotely owned device has live temporary values.
    control.settings["2"] = asdict(PROFILE)
    control._pre_remote[2] = PreRemoteSnapshot(25, True, NEXT)
    await takeover(control)
    assert saved(control)["settings"]["1"] == asdict(USER)
    assert saved(control)["settings"]["2"] == asdict(NEXT)
    assert control.settings["2"] == asdict(PROFILE)
    await control.async_release_remote_control(1, OWNER)
    assert saved(control)["settings"]["2"] == asdict(NEXT)
    assert control._pre_remote[2].power_settings == NEXT
