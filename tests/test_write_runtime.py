"""Tests for the serialized Home Assistant register write runtime."""

import asyncio

import pytest

from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.models import DiscoveredModel
from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_policy import WritePolicy
from custom_components.fronius_pv_manager.write_runtime import (
    WriteDeviceNotConfiguredError,
    WriteInvalidValueError,
    WriteModelNotDiscoveredError,
    WriteNotApprovedError,
    WriteReadBackError,
    WriteTransportError,
    WriteVerificationMismatchError,
)
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport

MODEL_BASE = 41000


def register(name):
    """Return one fixed Model 124 register definition."""
    return next(item for item in MODEL_124.registers if item.name == name)


class RuntimeTransport(FakeTransport):
    """Synthetic writable transport with controllable write verification."""

    def __init__(
        self,
        *,
        update_after_write: bool = True,
        write_error: bool = False,
        read_back_error: bool = False,
    ) -> None:
        values = {MODEL_BASE + offset: 0 for offset in range(24)}
        values[MODEL_BASE + register("MinRsvPct_SF").offset] = 0xFFFE
        values[MODEL_BASE + register("MinRsvPct").offset] = 700
        super().__init__(values)
        self.update_after_write = update_after_write
        self.write_error = write_error
        self.read_back_error = read_back_error
        self.write_calls: list[tuple[int, tuple[int, ...]]] = []

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Optionally fail only the verification read after one write."""
        if self.read_back_error and self.write_calls:
            raise ModbusTransportError("read-back failed")
        return super().read_holding_registers(address, count)

    def write_holding_registers(self, address: int, values) -> None:
        """Record one write attempt and optionally update live words."""
        words = tuple(values)
        self.write_calls.append((address, words))
        if self.write_error:
            raise ModbusTransportError("write failed")
        if self.update_after_write:
            for offset, word in enumerate(words):
                self.registers[address + offset] = word


class RecordingCoordinator(FroniusPVCoordinator):
    """Coordinator whose requested refreshes are observable without polling."""

    def __init__(self, hass, transport) -> None:
        policies = {(124, "MinRsvPct"): WritePolicy(124, "MinRsvPct", 0, 100)}
        super().__init__(hass, FakeEntry({}), {1: transport}, policies)
        self.discovered_models_by_device = {
            1: (DiscoveredModel(124, MODEL_BASE, 24),)
        }
        self.refresh_requests = 0

    async def async_request_refresh(self) -> None:
        """Record the post-write refresh request."""
        self.refresh_requests += 1


class YieldingHass(FakeHass):
    """Yield executor jobs so tests can observe attempted concurrency."""

    def __init__(self) -> None:
        super().__init__()
        self.active_io = 0
        self.maximum_active_io = 0

    async def async_add_executor_job(self, target, *args):
        """Track overlapping write/poll jobs before executing synchronously."""
        self.executor_jobs.append(target)
        if target.__name__ in {"_write_once", "_poll_devices"}:
            self.active_io += 1
            self.maximum_active_io = max(self.maximum_active_io, self.active_io)
            await asyncio.sleep(0.01)
            try:
                return target(*args)
            finally:
                self.active_io -= 1
        return target(*args)


def runtime(*, hass=None, transport=None):
    """Return a configured coordinator and writable synthetic transport."""
    transport = transport or RuntimeTransport()
    coordinator = RecordingCoordinator(hass or FakeHass(), transport)
    return coordinator, transport


@pytest.mark.asyncio
async def test_successful_write_verifies_once_and_requests_refresh() -> None:
    """An approved semantic value is written once and refreshed after verify."""
    coordinator, transport = runtime()

    result = await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)

    assert result.device_id == 1
    assert result.policy.register_name == "MinRsvPct"
    assert result.verified
    assert transport.write_calls == [
        (MODEL_BASE + register("MinRsvPct").offset, (1000,))
    ]
    assert coordinator.refresh_requests == 1


@pytest.mark.asyncio
async def test_live_scale_factor_is_read_before_every_write() -> None:
    """Neither policy nor runtime caches the current scale factor or address."""
    coordinator, transport = runtime()
    scale_address = MODEL_BASE + register("MinRsvPct_SF").offset

    await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)
    transport.registers[scale_address] = 0xFFFF
    await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 20)

    assert transport.read_calls.count((scale_address, 1)) == 2
    assert [words for _, words in transport.write_calls] == [(1000,), (200,)]


@pytest.mark.asyncio
async def test_lookup_and_policy_failures_are_distinct_and_do_not_write() -> None:
    """Device, topology, allow-list, and value failures have distinct errors."""
    coordinator, transport = runtime()
    with pytest.raises(WriteDeviceNotConfiguredError):
        await coordinator.write_runtime.async_write(2, 124, "MinRsvPct", 10)
    with pytest.raises(WriteModelNotDiscoveredError):
        await coordinator.write_runtime.async_write(1, 123, "WMaxLimPct", 10)
    with pytest.raises(WriteNotApprovedError):
        await coordinator.write_runtime.async_write(1, 124, "OutWRte", 10)
    with pytest.raises(WriteNotApprovedError):
        await coordinator.write_runtime.async_write(1, 124, "ChaState", 10)
    for value in (-1, 101):
        with pytest.raises(WriteInvalidValueError):
            await coordinator.write_runtime.async_write(
                1, 124, "MinRsvPct", value
            )
    assert transport.write_calls == []


@pytest.mark.asyncio
async def test_write_transport_failure_is_attempted_once_without_refresh() -> None:
    """An uncertain transport write is never retried automatically."""
    coordinator, transport = runtime(transport=RuntimeTransport(write_error=True))
    original_data = object()
    coordinator.data = original_data

    with pytest.raises(WriteTransportError) as raised:
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)

    assert isinstance(raised.value.__cause__, ModbusTransportError)
    assert len(transport.write_calls) == 1
    assert coordinator.refresh_requests == 0
    assert coordinator.data is original_data


@pytest.mark.asyncio
async def test_read_back_failure_is_distinct_and_not_retried() -> None:
    """A failed verification read surfaces separately after one write."""
    coordinator, transport = runtime(
        transport=RuntimeTransport(read_back_error=True)
    )

    with pytest.raises(WriteReadBackError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)

    assert len(transport.write_calls) == 1
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_verification_mismatch_is_failure_without_data_mutation() -> None:
    """A semantic mismatch never reports success or alters coordinator data."""
    coordinator, transport = runtime(
        transport=RuntimeTransport(update_after_write=False)
    )
    original_data = object()
    coordinator.data = original_data

    with pytest.raises(WriteVerificationMismatchError):
        await coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10)

    assert len(transport.write_calls) == 1
    assert coordinator.refresh_requests == 0
    assert coordinator.data is original_data


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialized() -> None:
    """Two writes cannot overlap within one config-entry runtime."""
    hass = YieldingHass()
    coordinator, transport = runtime(hass=hass)

    await asyncio.gather(
        coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10),
        coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 20),
    )

    assert hass.maximum_active_io == 1
    assert len(transport.write_calls) == 2


@pytest.mark.asyncio
async def test_write_does_not_overlap_polling_on_shared_transport() -> None:
    """Coordinator polling and writes share the same config-entry I/O lock."""
    hass = YieldingHass()
    coordinator, transport = runtime(hass=hass)

    await asyncio.gather(
        coordinator.write_runtime.async_write(1, 124, "MinRsvPct", 10),
        coordinator._async_update_data(),
    )

    assert hass.maximum_active_io == 1
    assert len(transport.write_calls) == 1
