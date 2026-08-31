"""Shared lightweight Home Assistant runtime test doubles."""

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntryState

from custom_components.fronius_pv_manager.sunspec import (
    SUNSPEC_BASE_TRANSPORT_ADDRESS,
)
from custom_components.fronius_pv_manager.transport import (
    ModbusConnectionError,
    ModbusTransportError,
)


class FakeHass:
    """Provide executor delegation required by the coordinator."""

    def __init__(self) -> None:
        self.executor_jobs: list[Callable] = []
        self.is_stopping = False

    async def async_add_executor_job(self, target, *args):
        """Record and execute one submitted synchronous callable."""
        self.executor_jobs.append(target)
        return target(*args)


class FakeEntry:
    """Provide config-entry data, runtime storage, and unload registration."""

    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.unload_callbacks: list[Callable] = []

    def async_on_unload(self, callback):
        """Record a coordinator shutdown callback."""
        self.unload_callbacks.append(callback)


class FakeTransport:
    """Provide synthetic SunSpec registers and configurable transport failures."""

    def __init__(
        self,
        registers: dict[int, int],
        *,
        connection_error: bool = False,
        close_error: bool = False,
    ) -> None:
        self.registers = registers
        self.connection_error = connection_error
        self.close_error = close_error
        self.fail_reads = False
        self.connect_calls = 0
        self.close_calls = 0
        self.read_calls: list[tuple[int, int]] = []

    def connect(self) -> None:
        """Record connection or raise a configured temporary failure."""
        self.connect_calls += 1
        if self.connection_error:
            raise ModbusConnectionError("connection unavailable")

    def close(self) -> None:
        """Record closure or raise a configured close failure."""
        self.close_calls += 1
        if self.close_error:
            raise ModbusTransportError("close failed")

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Return one synthetic range or raise a configured polling failure."""
        self.read_calls.append((address, count))
        if self.fail_reads:
            raise ModbusTransportError("read failed")
        return tuple(self.registers[address + offset] for offset in range(count))


def model_chain(*models: tuple[int, int]) -> tuple[dict[int, int], dict[int, int]]:
    """Build a synthetic chain and return model IDs mapped to payload bases."""
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS
    registers = {base: 0x5375, base + 1: 0x6E53}
    payload_bases = {}
    header = base + 2
    for model_id, length in models:
        registers[header] = model_id
        registers[header + 1] = length
        payload_base = header + 2
        payload_bases[model_id] = payload_base
        registers.update(
            {payload_base + offset: 0 for offset in range(length)}
        )
        header = payload_base + length
    registers[header] = 0xFFFF
    registers[header + 1] = 0
    return registers, payload_bases
