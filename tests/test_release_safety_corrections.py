"""Production runtime regressions for the final release-safety findings."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.fronius_pv_manager import async_setup_entry, async_unload_entry
from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.solar_api import SolarState
from custom_components.fronius_pv_manager.topology import (
    model_topology,
    validate_topology,
)
from custom_components.fronius_pv_manager.transport import ModbusTcpEndpointTransport
from custom_components.fronius_pv_manager.write_policy_loader import (
    DEFAULT_POLICY_PATH,
    load_write_policy_text,
)
from custom_components.fronius_pv_manager.write_runtime import (
    FroniusPVWriteError,
    WriteSequenceError,
)
from tests.runtime_fakes import FakeEntry, FakeHass
from tests.test_init import install_endpoint_factory
from tests.test_release_readiness import WritableDevice

OWNER = "review-owner"


class DeviceClient:
    """Fake only the pymodbus client; use the real endpoint and runtime above it."""

    def __init__(self):
        self.device = WritableDevice()
        self.connected = False
        self.writes = []
        self.on_read = None
        self.on_write = None
        self.connects = 0

    def connect(self):
        self.connected = True
        self.connects += 1
        return True

    def close(self):
        self.connected = False

    def read_holding_registers(self, address, *, count, device_id):
        if self.on_read is not None:
            self.on_read(address)
        return SimpleNamespace(
            registers=[self.device.registers[address + i] for i in range(count)],
            isError=lambda: False,
        )

    def write_register(self, address, value, *, device_id):
        self.writes.append((address, value))
        self.device.registers[address] = value
        if self.on_write is not None:
            self.on_write()
        return SimpleNamespace(isError=lambda: False)


async def live_runtime():
    client = DeviceClient()
    endpoint = ModbusTcpEndpointTransport("192.0.2.1")
    endpoint._client = client
    coordinator = FroniusPVCoordinator(
        FakeHass(),
        FakeEntry({}),
        {1: endpoint.bind(1)},
        load_write_policy_text(DEFAULT_POLICY_PATH.read_text()),
    )
    coordinator.solar_api.async_poll = AsyncMock(return_value=SolarState())
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_refresh()
    return coordinator, endpoint, client


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_offset", [5, 15])
async def test_cleanup_reset_relocation_requires_fresh_explicit_retry(failed_offset):
    coordinator, endpoint, client = await live_runtime()
    control = coordinator.storage_control
    try:
        await control.async_acquire_remote_control(1, OWNER)
        snapshot = control._pre_remote[1]
        previous_target = dict(control.last_targets["1"])
        old_base = client.device.bases[124]
        assert old_base == 40056
        client.writes.clear()

        def disconnect_and_move(address):
            if address == old_base + failed_offset:
                client.on_read = None
                client.device = WritableDevice(((113, 60), (124, 24)))
                client.device.registers[client.device.bases[124] + 3] = 3
                raise OSError("disconnect during restoration preflight")

        client.on_read = disconnect_and_move
        generation = endpoint.generation
        with pytest.raises(WriteSequenceError, match="authority invalidated") as error:
            await control.async_release_remote_control(1, OWNER)
        assert endpoint.generation != generation
        assert coordinator.live_models(1) == ()
        assert client.writes == []
        assert control.mode(1) == "remote"
        assert control.last_targets["1"] == previous_target
        assert control._pre_remote[1] is snapshot
        assert control._leases[1].owner_id == OWNER
        assert control._cleanup[1].verified_steps == 0
        assert not error.value.completed
        assert error.value.failed_register == (
            "MinRsvPct" if failed_offset == 5 else "ChaGriSet"
        )
        assert not control._watchdogs

        await coordinator.async_refresh()
        new_base = client.device.bases[124]
        assert new_base == 40066
        assert coordinator.live_models(1)[-1].base_address == new_base
        assert not client.writes  # Discovery never retries the failed cleanup.
        await control.async_release_remote_control(1, OWNER)
        assert client.writes == [
            (new_base + 3, 0),
            (new_base + 11, 100),
            (new_base + 10, 100),
            (new_base + 5, 20),
            (new_base + 15, 0),
        ]
        assert control.mode(1) == "automatic"
        assert not control._leases and not control._cleanup and not control._pre_remote
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
async def test_deferred_policy_failure_keeps_valid_neutral_prefix():
    coordinator, endpoint, client = await live_runtime()
    control = coordinator.storage_control
    try:
        await control.async_acquire_remote_control(1, OWNER)
        policy = coordinator.write_policies[(124, "MinRsvPct")]
        coordinator.write_policies = {
            **coordinator.write_policies,
            (124, "MinRsvPct"): replace(policy, enabled=False),
        }
        client.writes.clear()
        authority = coordinator.write_authority(1)
        with pytest.raises(WriteSequenceError) as error:
            await control.async_release_remote_control(1, OWNER)
        base = client.device.bases[124]
        assert client.writes == [(base + 3, 0), (base + 11, 100), (base + 10, 100)]
        assert len(error.value.completed) == 3
        assert error.value.failed_register == "MinRsvPct"
        assert coordinator.write_authority(1) == authority
        assert control.mode(1) == "automatic"
        assert control._pre_remote and control._cleanup and control._leases
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["single", "sequence"])
@pytest.mark.parametrize("invalidation", ["reset", "rediscover", "expire", "remove"])
async def test_preparation_cannot_outlive_its_authority(operation, invalidation):
    coordinator, endpoint, client = await live_runtime()
    try:

        def invalidate(_address):
            client.on_read = None
            if invalidation == "reset":
                endpoint.reset()
            elif invalidation == "rediscover":
                # Even an identical layout discovered again is new authority.
                coordinator._discover_device(1, coordinator.transports[1])
            elif invalidation == "expire":
                coordinator._discovery_deadlines[1] = 0
            else:
                coordinator.discovered_models_by_device.pop(1)

        client.on_read = invalidate
        with pytest.raises(FroniusPVWriteError):
            if operation == "single":
                await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
            else:
                await coordinator.write_runtime.async_write_sequence(
                    1, [(124, "StorCtl_Mod", 0), (124, "MinRsvPct", 25)]
                )
        assert client.writes == []
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
async def test_invalidation_during_physical_write_stops_remaining_sequence():
    coordinator, endpoint, client = await live_runtime()
    try:
        client.on_write = endpoint.reset
        with pytest.raises(WriteSequenceError) as error:
            await coordinator.write_runtime.async_write_sequence(
                1, [(124, "StorCtl_Mod", 0), (124, "MinRsvPct", 25)]
            )
        assert len(client.writes) == 1  # Uncertain first write is never retried.
        assert error.value.completed == ()
        assert error.value.failed_register == "StorCtl_Mod"
        assert coordinator.live_models(1) == ()
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
async def test_client_session_loss_cannot_transparently_reconnect_a_write():
    coordinator, endpoint, client = await live_runtime()
    try:
        connects = client.connects
        client.connected = False
        with pytest.raises(FroniusPVWriteError):
            await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
        assert not client.writes
        assert client.connects == connects
        assert not coordinator.live_models(1)
        await coordinator.async_refresh()
        assert client.connects == connects + 1
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_model", [["model_id", "base_address", "length"], None, 1])
async def test_malformed_model_preserves_siblings_and_offline_setup(
    bad_model, monkeypatch, caplog
):
    coordinator, _, _ = await live_runtime()
    good = model_topology(coordinator.live_models(1)[-1])
    await coordinator.async_stop()
    bad = {"model": bad_model, "fixed": {}, "repeating": {}}
    saved = {"1": [bad, good], "2": [good]}
    assert validate_topology(saved) == {"1": [good], "2": [good]}
    device = WritableDevice()
    device.fail_reads = True
    install_endpoint_factory(monkeypatch, {1: device, 2: device})
    hass = FakeHass()
    entry = FakeEntry({"host": "192.0.2.1", "device_ids": [1, 2], "topology": saved})
    try:
        assert await async_setup_entry(hass, entry)
        assert len(entry.runtime_data.entity_data.devices) == 2
        assert all(d.discovered_models for d in entry.runtime_data.entity_data.devices)
        assert not device.write_calls
        assert "Ignoring malformed topology record" in caplog.text
    finally:
        await async_unload_entry(hass, entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("scalar", ["2026-99-99", "1" + "0" * 400])
async def test_bad_policy_scalars_preserve_readable_setup(
    scalar, monkeypatch, tmp_path, caplog
):
    directory = tmp_path / "fronius_pv_manager"
    directory.mkdir()
    policy = directory / "write_policy.yaml"
    content = (
        "version: 1\nmodels:\n  124:\n    MinRsvPct:\n      minimum: " + scalar + "\n"
    )
    policy.write_text(content)
    device = WritableDevice()
    install_endpoint_factory(monkeypatch, {1: device})
    hass = FakeHass(config_dir=tmp_path)
    entry = FakeEntry({"host": "192.0.2.1", "device_id": 1})
    try:
        assert await async_setup_entry(hass, entry)
        coordinator = entry.runtime_data
        assert coordinator.data.devices[0].available
        assert not coordinator.write_policies
        with pytest.raises(FroniusPVWriteError):
            await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 25)
        assert not device.write_calls
        assert policy.read_text() == content
        assert "writes are disabled" in caplog.text
    finally:
        await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_session_loss_after_preflight_prevents_first_physical_write():
    coordinator, endpoint, client = await live_runtime()
    try:

        def lose_session(_transport, _models):
            client.connected = False

        connects = client.connects
        with pytest.raises(WriteSequenceError) as error:
            await coordinator.write_runtime.async_write_sequence(
                1, [(124, "StorCtl_Mod", 0)], before_write=lose_session
            )
        assert not client.writes
        assert error.value.completed == ()
        assert client.connects == connects
        assert not coordinator.live_models(1)
    finally:
        await coordinator.async_stop()


@pytest.mark.asyncio
async def test_expiry_between_verified_steps_stops_without_repeating(monkeypatch):
    from custom_components.fronius_pv_manager import write_runtime

    coordinator, endpoint, client = await live_runtime()
    execute = write_runtime.execute_register_write

    def expire_after_step(transport, plan):
        result = execute(transport, plan)
        coordinator._discovery_deadlines[1] = 0
        return result

    monkeypatch.setattr(write_runtime, "execute_register_write", expire_after_step)
    try:
        with pytest.raises(WriteSequenceError) as error:
            await coordinator.write_runtime.async_write_sequence(
                1, [(124, "StorCtl_Mod", 0), (124, "MinRsvPct", 25)]
            )
        assert client.writes == [(client.device.bases[124] + 3, 0)]
        assert len(error.value.completed) == 1
        assert error.value.failed_register == "MinRsvPct"
    finally:
        await coordinator.async_stop()
