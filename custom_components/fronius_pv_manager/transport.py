"""Home Assistant-independent Modbus TCP transport.

The public API in this module accepts only zero-based pymodbus transport
addresses. Fronius and SunSpec documentation commonly uses one-based register
numbers such as 40001; conversion from those documentation numbers belongs at
the boundary that interprets the documentation. Model-relative offsets are a
third address space and must be combined with a discovered model base before a
transport read is made.
"""

from collections.abc import Sequence
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


class ModbusTcpTransport:
    """Minimal synchronous Modbus TCP transport using pymodbus's public API."""

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        device_id: int = DEFAULT_UNIT_ID,
    ) -> None:
        """Initialize a transport without opening the connection."""
        self._device_id = device_id
        self._client = ModbusTcpClient(
            host,
            port=port,
            retries=0,
            reconnect_delay=0,
        )

    def connect(self) -> None:
        """Open the TCP connection or raise an integration-specific error."""
        try:
            connected = self._client.connect()
        except Exception as err:
            raise ModbusConnectionError(
                "failed to connect to Modbus TCP device"
            ) from err
        if not connected:
            raise ModbusConnectionError("failed to connect to Modbus TCP device")

    def close(self) -> None:
        """Close the TCP connection."""
        try:
            self._client.close()
        except Exception as err:
            raise ModbusTransportError("failed to close Modbus TCP connection") from err

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read holding registers from a zero-based pymodbus address."""
        if address < 0:
            raise ValueError("transport address must not be negative")
        if count <= 0:
            raise ValueError("register count must be positive")

        try:
            response = self._client.read_holding_registers(
                address,
                count=count,
                device_id=self._device_id,
            )
        except Exception as err:
            raise ModbusTransportError("holding-register read failed") from err

        if response.isError():
            raise ModbusTransportError("Modbus device returned an error response")
        try:
            registers = tuple(response.registers)
        except (AttributeError, TypeError) as err:
            raise ModbusTransportError("Modbus response contains no registers") from err
        if len(registers) != count:
            raise ModbusTransportError(
                f"expected {count} registers but received {len(registers)}"
            )
        return registers

    def write_holding_registers(
        self, address: int, values: Sequence[int]
    ) -> None:
        """Write one contiguous range at a zero-based pymodbus address."""
        if address < 0:
            raise ValueError("transport address must not be negative")
        words = tuple(values)
        if not words:
            raise ValueError("register values must not be empty")
        if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
            raise ValueError("register values must be integers from 0 through 65535")

        try:
            if len(words) == 1:
                response = self._client.write_register(
                    address, words[0], device_id=self._device_id
                )
            else:
                response = self._client.write_registers(
                    address, list(words), device_id=self._device_id
                )
        except Exception as err:
            raise ModbusTransportError("holding-register write failed") from err
        if response.isError():
            raise ModbusTransportError("Modbus device returned an error response")


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
