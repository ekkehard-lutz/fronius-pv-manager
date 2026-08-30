"""Home Assistant-independent Modbus TCP transport.

The public API in this module accepts only zero-based pymodbus transport
addresses. Fronius and SunSpec documentation commonly uses one-based register
numbers such as 40001; conversion from those documentation numbers belongs at
the boundary that interprets the documentation. Model-relative offsets are a
third address space and must be combined with a discovered model base before a
transport read is made.
"""

from pymodbus.client import ModbusTcpClient

from .const import DEFAULT_PORT, DEFAULT_UNIT_ID


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
