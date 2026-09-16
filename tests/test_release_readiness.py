"""Stabilization regressions using real executor and HA storage boundaries."""

import asyncio
import threading
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import storage

from custom_components.fronius_pv_manager import (
    async_setup_entry,
    async_unload_entry,
    binary_sensor,
    sensor,
)
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.solar_entity import SolarEntity
from custom_components.fronius_pv_manager.storage_control import (
    PowerSettings,
    StoragePersistenceError,
)
from custom_components.fronius_pv_manager.sunspec import SunSpecDiscovery
from custom_components.fronius_pv_manager.topology import (
    model_topology,
    validate_topology,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_policy_loader import (
    DEFAULT_POLICY_PATH,
    WritePolicyLoadError,
    load_write_policy_text,
)
from custom_components.fronius_pv_manager.write_runtime import (
    WriteModelNotDiscoveredError,
)
from tests.runtime_fakes import (
    FakeConfigEntries,
    FakeEntry,
    FakeHass,
    FakeTransport,
    model_chain,
)
from tests.test_init import install_endpoint_factory
from tests.test_storage_quantization import hardware
from tests.test_storage_remote import OWNER, PROFILE
from tests.test_storage_remote import remote as remote_fixture

remote = remote_fixture


class WritableDevice(FakeTransport):
    def __init__(self, models=((103, 50), (124, 24))):
        registers, self.bases = model_chain(*models)
        super().__init__(registers)
        self.write_calls = []
        if 124 in self.bases:
            base = self.bases[124]
            self.registers.update({base: 6000, base + 5: 20, base + 15: 0})

    def write_holding_registers(self, address, words):
        self.write_calls.append((address, tuple(words)))
        self.registers.update(
            {address + index: word for index, word in enumerate(words)}
        )


def runtime(device=None, topology=None):
    device = device or WritableDevice()
    entry = FakeEntry({"topology": topology or {}})
    coordinator = FroniusPVCoordinator(
        FakeHass(),
        entry,
        {1: device},
        load_write_policy_text(DEFAULT_POLICY_PATH.read_text()),
    )
    entry.runtime_data = coordinator
    coordinator.async_request_refresh = AsyncMock()
    return coordinator, device, entry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["single", "sequence", "poll", "discover", "close"]
)
@pytest.mark.parametrize("worker_failure", [False, True])
async def test_real_worker_cancellation_drains_before_competitors(
    operation, worker_failure
):
    coordinator, device, _ = runtime()
    await coordinator.async_refresh()
    entered, finish = threading.Event(), threading.Event()
    competing_entered = threading.Event()
    names = {
        "single": "_write_once",
        "sequence": "_sequence_once",
        "poll": "_poll_devices",
        "discover": "_connect_and_discover",
        "close": "_close_transports",
    }

    competitor_name = "_sequence_once" if operation == "poll" else "_poll_devices"

    def executor(target, *args):
        def run():
            if target.__name__ == competitor_name:
                competing_entered.set()
            if target.__name__ == names[operation]:
                entered.set()
                assert finish.wait(5), "test worker was not released"
                if worker_failure:
                    raise ModbusTransportError("controlled worker failure")
            return target(*args)

        return asyncio.get_running_loop().run_in_executor(None, run)

    coordinator.hass.async_add_executor_job = executor
    calls = {
        "single": lambda: coordinator.write_runtime.async_write(
            1, 124, "MinRsvPct", 25
        ),
        "sequence": lambda: coordinator.write_runtime.async_write_sequence(
            1, [(124, "MinRsvPct", 25)]
        ),
        "poll": coordinator._async_update_data,
        "discover": coordinator.async_discover,
        "close": coordinator.async_close,
    }
    task = asyncio.create_task(calls[operation]())
    competitor = None
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated cancellation must also retain ownership.
        if operation == "close":
            # New writes are rejected during close; a second close must still
            # queue behind the active worker.
            competing = coordinator.async_run_io(competing_entered.set, closing=True)
        elif operation == "poll":
            competing = coordinator.write_runtime.async_write_sequence(
                1, [(124, "MinRsvPct", 30)]
            )
        else:
            competing = coordinator._async_update_data()
        competitor = asyncio.create_task(competing)
        await asyncio.sleep(0.02)
        assert coordinator.io_lock.locked()
        assert not task.done()
        assert not competing_entered.is_set()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        if competitor:
            await asyncio.wait_for(competitor, 2)
        await asyncio.wait_for(coordinator.async_stop(), 2)
    assert competing_entered.is_set()
    assert not coordinator.io_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stop", [False, True])
async def test_stop_drains_real_write_and_rejects_queued_write(cancel_stop):
    coordinator, device, _ = runtime()
    await coordinator.async_refresh()
    entered, finish = threading.Event(), threading.Event()
    original = device.write_holding_registers

    def blocking_write(address, words):
        entered.set()
        assert finish.wait(5)
        original(address, words)

    device.write_holding_registers = blocking_write

    def executor(fn, *args):
        return asyncio.get_running_loop().run_in_executor(None, fn, *args)

    coordinator.hass.async_add_executor_job = executor
    task = asyncio.create_task(
        coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    )
    async with asyncio.timeout(3):
        while not entered.is_set():
            await asyncio.sleep(0.001)
    stop = asyncio.create_task(coordinator.async_stop())
    try:
        await asyncio.sleep(0.02)
        assert not stop.done()
        assert device.close_calls == 0
        if cancel_stop:
            stop.cancel()
            await asyncio.sleep(0)
            stop.cancel()
            assert not stop.done()
        late = asyncio.create_task(
            coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 30)
        )
    finally:
        finish.set()
    await task
    if cancel_stop:
        with pytest.raises(asyncio.CancelledError):
            await stop
    else:
        await stop
    with pytest.raises(ServiceValidationError, match="shutting down"):
        await late
    assert len(device.write_calls) == 1
    assert device.close_calls == 1
    await coordinator.async_stop()
    assert device.close_calls == 1


@pytest.mark.asyncio
async def test_changed_cached_topology_is_not_write_authority():
    old = WritableDevice()
    cached = {"1": [model_topology(m) for m in SunSpecDiscovery(old).discover()]}
    new = WritableDevice(((113, 60), (124, 24)))
    coordinator, device, entry = runtime(new, cached)
    assert coordinator.entity_data.devices[0].discovered_models
    with pytest.raises(WriteModelNotDiscoveredError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    assert not device.write_calls
    await coordinator.async_refresh()
    assert not device.write_calls
    assert coordinator.live_models(1)[-1].base_address == new.bases[124]
    await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    assert device.write_calls == [(new.bases[124] + 5, (25,))]
    assert entry.data["topology"]["1"][-1]["model"]["base_address"] == new.bases[124]
    assert old.bases[124] != new.bases[124]
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_failed_discovery_and_expired_validation_block_writes():
    coordinator, device, _ = runtime()
    await coordinator.async_refresh()
    reads = len(device.read_calls)
    await coordinator.async_refresh()
    assert (40000, 2) not in device.read_calls[reads:]
    coordinator._discovery_deadlines[1] = 0
    with pytest.raises(WriteModelNotDiscoveredError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    device.registers[40000] = 0
    await coordinator.async_refresh()
    assert not coordinator.data.devices[0].available
    with pytest.raises(WriteModelNotDiscoveredError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    assert not device.write_calls
    device.registers[40000] = 0x5375
    await coordinator.async_refresh()
    assert coordinator.data.devices[0].available
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_reconnection_invalidates_live_addresses():
    coordinator, device, _ = runtime()
    device.generation = 0
    await coordinator.async_refresh()
    device.generation += 1
    with pytest.raises(WriteModelNotDiscoveredError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    await coordinator.async_refresh()
    await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
    assert len(device.write_calls) == 1
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_real_store_suppressed_failure_keeps_cleanup_retry(remote, monkeypatch):
    coordinator, control, clock = remote
    original = control.values(1)
    await control.async_acquire_remote_control(1, OWNER, PROFILE)
    write = storage.write_utf8_file_atomic

    def fail(*args, **kwargs):
        raise storage.WriteError("simulated disk full")

    monkeypatch.setattr(storage, "write_utf8_file_atomic", fail)
    with pytest.raises(StoragePersistenceError, match="not persisted"):
        await control.async_release_remote_control(1, OWNER)
    assert control.mode(1) == "automatic"
    assert control._pre_remote[1].power_settings == original
    assert control._cleanup[1].failed_step == "persistence"
    assert control._leases and not clock.active
    monkeypatch.setattr(storage, "write_utf8_file_atomic", write)
    await control.async_release_remote_control(1, OWNER)
    assert not control._leases and not control._pre_remote
    assert control.values(1) == original


@pytest.mark.asyncio
async def test_identical_save_failure_is_detected_by_revision(monkeypatch):
    coordinator, control = hardware()
    await control.async_change(1, field="maximum_charge_power", value=5000)

    def fail(*args, **kwargs):
        raise storage.WriteError("simulated disk full")

    monkeypatch.setattr(storage, "write_utf8_file_atomic", fail)
    with pytest.raises(StoragePersistenceError):
        await control.async_change(1, field="maximum_charge_power", value=5000)
    await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        [],
        1,
        "wrong",
        {"settings": None},
        {"settings": {"1": {"unknown": 1}}, "modes": []},
        {"settings": {"1": asdict(PowerSettings(True, 100, 0, 100))}},
        {"settings": {}, "last_targets": {"1": []}},
    ],
)
async def test_bad_storage_records_do_not_break_setup_or_write(bad, caplog):
    coordinator, control = hardware()
    control.store.async_load = AsyncMock(return_value=bad)
    await control.async_load()
    assert not coordinator.control_transport.write_calls
    assert not control.settings
    assert "Ignoring malformed" in caplog.text
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_good_storage_records_survive_bad_sibling():
    coordinator, control = hardware()
    profile = asdict(PowerSettings(0, 100, 0, 200))
    control.store.async_load = AsyncMock(
        return_value={"settings": {"1": profile, "2": []}}
    )
    await control.async_load()
    assert control.settings == {"1": profile}
    await coordinator.async_stop()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"1": None},
        {"1": [None]},
        {"1": [{"model": {"model_id": True, "base_address": 4, "length": 24}}]},
    ],
)
def test_bad_topology_is_discarded(bad):
    assert validate_topology(bad) == {}


@pytest.mark.asyncio
async def test_automatic_without_reference_does_not_invent_profile():
    coordinator, control = hardware()
    coordinator.control_transport.registers[41000] = 65535
    coordinator.data = coordinator._snapshot()
    await control.async_change(1, mode="automatic")
    assert control.mode(1) == "automatic"
    assert not control.settings
    assert [words for _, words in coordinator.control_transport.write_calls] == [
        (0,),
        (10000,),
        (10000,),
    ]
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_solar_roles_and_later_discovery_keep_identities():
    coordinator, device, entry = runtime(WritableDevice(((103, 50),)))
    await coordinator.async_refresh()
    entities = []
    await sensor.async_setup_entry(coordinator.hass, entry, entities.extend)
    await binary_sensor.async_setup_entry(coordinator.hass, entry, entities.extend)

    def solar():
        return [e for e in entities if isinstance(e, SolarEntity)]

    assert {e.key for e in solar()} == {"pv_power", "backup_mode"}
    original = {e.key: e.unique_id for e in solar()}
    replacement = WritableDevice()
    device.registers = replacement.registers
    coordinator._discovery_deadlines[1] = 0
    await coordinator.async_refresh()
    assert {e.key for e in solar()} == {
        "pv_power",
        "backup_mode",
        "battery_standby",
        "battery_operation_mode",
    }
    assert all(e.unique_id == original[e.key] for e in solar() if e.key in original)
    device.fail_reads = True
    await coordinator.async_refresh()
    device.fail_reads = False
    await coordinator.async_refresh()
    assert len(solar()) == 4
    await coordinator.async_stop()


@pytest.mark.parametrize("key", ["[124]", "{a: b}", "true", "1.2", "null"])
def test_unsupported_policy_mapping_keys_normalized(key):
    with pytest.raises(WritePolicyLoadError):
        load_write_policy_text(f"version: 1\nmodels:\n  ? {key}\n  : {{}}\n")


@pytest.mark.asyncio
async def test_bad_policy_does_not_disable_read_setup(monkeypatch, tmp_path):
    directory = tmp_path / "fronius_pv_manager"
    directory.mkdir()
    (directory / "write_policy.yaml").write_text(
        "version: 1\nmodels:\n  ? [124]\n  : {}\n"
    )
    install_endpoint_factory(monkeypatch, {1: WritableDevice()})
    hass, entry = (
        FakeHass(config_dir=tmp_path),
        FakeEntry({"host": "192.0.2.1", "device_id": 1}),
    )
    await async_setup_entry(hass, entry)
    assert not entry.runtime_data.write_policies
    assert entry.runtime_data.data.devices[0].available
    await async_unload_entry(hass, entry)
    assert await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_ha_stop_event_without_entry_unload(monkeypatch, tmp_path):
    endpoint, _ = install_endpoint_factory(monkeypatch, {1: WritableDevice()})
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = FakeConfigEntries()
    entry = FakeEntry({"host": "192.0.2.1", "device_id": 1})
    await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.storage_control.async_acquire_remote_control(1, OWNER)
    before = list(endpoint.transports[1].write_calls)
    hass.set_state(CoreState.stopping)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert not hass.config_entries.unloaded
    assert coordinator.storage_control._closed
    assert not coordinator.storage_control._watchdogs
    assert endpoint.transports[1].write_calls == before
    assert endpoint.close_calls == 1
    with pytest.raises(ServiceValidationError, match="unloading"):
        await coordinator.storage_control.async_remote_heartbeat(1, OWNER)
    await async_unload_entry(hass, entry)
    assert endpoint.close_calls == 1


@pytest.mark.asyncio
async def test_queued_write_resolves_new_live_address_under_lock():
    coordinator, device, _ = runtime()
    await coordinator.async_refresh()
    async with coordinator.io_lock:
        queued = asyncio.create_task(
            coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
        )
        await asyncio.sleep(0)
        replacement = WritableDevice(((113, 60), (124, 24)))
        device.registers = replacement.registers
        coordinator._discover_device(1, device)
    await queued
    assert device.write_calls == [(replacement.bases[124] + 5, (25,))]
    await coordinator.async_stop()


@pytest.mark.asyncio
async def test_deferred_store_save_is_not_confirmed(monkeypatch):
    coordinator, control = hardware()
    await control.async_change(1, field="maximum_charge_power", value=5000)
    coordinator.hass.state = CoreState.stopping
    with pytest.raises(StoragePersistenceError, match="not persisted"):
        await control._save({"settings": {}, "modes": {}, "last_targets": {}})
    # Exercise HA's final-write listener too, without pretending the earlier
    # normal async_save return had confirmed it.
    await coordinator.hass.bus.fire("homeassistant_final_write")
    await coordinator.async_stop()


def test_topology_preserves_good_sibling_and_rejects_nested_damage():
    device = WritableDevice()
    good = model_topology(SunSpecDiscovery(device).discover()[0])
    bad = {
        **good,
        "repeating": {"modules": [{"values": [], "instance_index": 0}]},
    }
    assert validate_topology({"1": [good, bad], "9" * 5000: []}) == {"1": [good]}


@pytest.mark.asyncio
async def test_stop_drains_inflight_supplemental_poll():
    coordinator, _, _ = runtime()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def poll():
        entered.set()
        await finish.wait()
        return coordinator.solar_state

    coordinator.solar_api.async_poll = poll
    refresh = asyncio.create_task(coordinator._async_update_data())
    await entered.wait()
    stop = asyncio.create_task(coordinator.async_stop())
    await asyncio.sleep(0)
    assert not stop.done()
    finish.set()
    await refresh
    await stop
    assert not coordinator._update_tasks
