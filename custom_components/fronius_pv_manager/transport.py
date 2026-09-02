"""Home Assistant-independent Modbus TCP transport.

The public API in this module accepts only zero-based pymodbus transport
addresses. Fronius and SunSpec documentation commonly uses one-based register
numbers such as 40001; conversion from those documentation numbers belongs at
the boundary that interprets the documentation. Model-relative offsets are a
third address space and must be combined with a discovered model base before a
transport read is made.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pymodbus.client import ModbusTcpClient

from .const import DEFAULT_PORT, DEFAULT_UNIT_ID

MAX_HOLDING_REGISTERS_PER_READ = 100


class HoldingRegisterReader(Protocol):
    """Object capable of reading zero-based Modbus holding registers."""

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read a contiguous range of holding registers."""
        ...


class ModbusTransportError(Exception):
    """Base exception for Modbus transport failures."""


class ModbusConnectionError(ModbusTransportError):
    """Raised when a Modbus TCP connection cannot be established."""


class ModbusTcpEndpointTransport:
    """Own one synchronous pymodbus client for one physical TCP endpoint."""

    def __init__(self, host: str, *, port: int = DEFAULT_PORT) -> None:
        """Initialize an endpoint without opening the connection."""
        self._client = ModbusTcpClient(
            host,
            port=port,
            retries=0,
            reconnect_delay=0,
        )
        self._connected = False

    def connect(self) -> None:
        """Open the TCP connection or raise an integration-specific error."""
        if self._connected:
            return
        try:
            connected = self._client.connect()
        except Exception as err:
            self._reset_safely()
            raise ModbusConnectionError(
                "failed to connect to Modbus TCP device"
            ) from err
        if not connected:
            self._reset_safely()
            raise ModbusConnectionError("failed to connect to Modbus TCP device")
        self._connected = True

    def close(self) -> None:
        """Close the TCP connection once; repeated closure is harmless."""
        if not self._connected:
            return
        try:
            self._client.close()
        except Exception as err:
            raise ModbusTransportError("failed to close Modbus TCP connection") from err
        finally:
            self._connected = False

    def reset(self) -> None:
        """Discard the current endpoint session without retrying a request."""
        self.close()

    def bind(self, device_id: int = DEFAULT_UNIT_ID) -> ModbusDeviceTransport:
        """Return a lightweight request view bound to one Modbus device ID."""
        return ModbusDeviceTransport(self, device_id)

    def _ensure_connected(self) -> None:
        """Connect a new session for a later independent request if needed."""
        if not self._connected:
            self.connect()

    def _reset_safely(self) -> None:
        """Reset without hiding the transport failure that triggered recovery."""
        try:
            self._client.close()
        except Exception:
            pass
        finally:
            self._connected = False

    def read_holding_registers(
        self, address: int, count: int, *, device_id: int
    ) -> tuple[int, ...]:
        """Read holding registers using explicit request device metadata."""
        if address < 0:
            raise ValueError("transport address must not be negative")
        if count <= 0:
            raise ValueError("register count must be positive")

        try:
            self._ensure_connected()
            response = self._client.read_holding_registers(
                address,
                count=count,
                device_id=device_id,
            )
        except Exception as err:
            self._reset_safely()
            raise ModbusTransportError("holding-register read failed") from err

        if response.isError():
            self._reset_safely()
            raise ModbusTransportError("Modbus device returned an error response")
        try:
            registers = tuple(response.registers)
        except (AttributeError, TypeError) as err:
            self._reset_safely()
            raise ModbusTransportError("Modbus response contains no registers") from err
        if len(registers) != count:
            self._reset_safely()
            raise ModbusTransportError(
                f"expected {count} registers but received {len(registers)}"
            )
        return registers

    def write_holding_registers(
        self, address: int, values: Sequence[int], *, device_id: int
    ) -> None:
        """Write one contiguous range using explicit request device metadata."""
        if address < 0:
            raise ValueError("transport address must not be negative")
        words = tuple(values)
        if not words:
            raise ValueError("register values must not be empty")
        if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
            raise ValueError("register values must be integers from 0 through 65535")

        try:
            self._ensure_connected()
            if len(words) == 1:
                response = self._client.write_register(
                    address, words[0], device_id=device_id
                )
            else:
                response = self._client.write_registers(
                    address, list(words), device_id=device_id
                )
        except Exception as err:
            self._reset_safely()
            raise ModbusTransportError("holding-register write failed") from err
        if response.isError():
            self._reset_safely()
            raise ModbusTransportError("Modbus device returned an error response")


@dataclass(frozen=True, slots=True)
class ModbusDeviceTransport:
    """Small register transport view bound to one endpoint device ID."""

    endpoint: ModbusTcpEndpointTransport
    device_id: int

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read through the shared endpoint with this view's device ID."""
        return self.endpoint.read_holding_registers(
            address, count, device_id=self.device_id
        )

    def write_holding_registers(
        self, address: int, values: Sequence[int]
    ) -> None:
        """Write through the shared endpoint with this view's device ID."""
        self.endpoint.write_holding_registers(
            address, values, device_id=self.device_id
        )


class ModbusTcpTransport:
    """Short-lived compatibility transport bound to one device ID.

    Runtime setup uses :class:`ModbusTcpEndpointTransport` directly so all
    configured device views share one client. Standalone tools may continue to
    use this convenient single-device owner.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        device_id: int = DEFAULT_UNIT_ID,
    ) -> None:
        self.endpoint = ModbusTcpEndpointTransport(host, port=port)
        self.device_id = device_id
        self._view = self.endpoint.bind(device_id)

    def connect(self) -> None:
        """Connect the owned short-lived endpoint."""
        self.endpoint.connect()

    def close(self) -> None:
        """Close the owned short-lived endpoint."""
        self.endpoint.close()

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read through the bound device view."""
        return self._view.read_holding_registers(address, count)

    def write_holding_registers(
        self, address: int, values: Sequence[int]
    ) -> None:
        """Write through the bound device view."""
        self._view.write_holding_registers(address, values)


def read_holding_registers_chunked(
    transport: HoldingRegisterReader,
    start_address: int,
    total_count: int,
) -> tuple[int, ...]:
    """Read a contiguous zero-based range in chunks of at most 100 registers."""
    if start_address < 0:
        raise ValueError("start_address must not be negative")
    if total_count < 0:
        raise ValueError("total_count must not be negative")

    registers: list[int] = []
    while len(registers) < total_count:
        address = start_address + len(registers)
        count = min(
            MAX_HOLDING_REGISTERS_PER_READ,
            total_count - len(registers),
        )
        registers.extend(transport.read_holding_registers(address, count))
    return tuple(registers)
