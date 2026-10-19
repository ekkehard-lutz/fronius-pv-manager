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
from pymodbus.exceptions import ConnectionException, ModbusIOException

from .const import DEFAULT_PORT, DEFAULT_UNIT_ID
from .diagnostic_logging import REPEAT_SECONDS, attach_modbus_logging, modbus_logging

MAX_HOLDING_REGISTERS_PER_READ = 100


class HoldingRegisterReader(Protocol):
    """Object capable of reading zero-based Modbus holding registers."""

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read a contiguous range of holding registers."""
        ...


class ModbusTransportError(Exception):
    """Base exception for Modbus transport failures."""

    def __init__(self, message, *, reason="transport_error"):
        super().__init__(message)
        self.reason = reason


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
        self.generation = 0
        self._diagnostics = None

    def set_diagnostics(self, diagnostics):
        """Attach the runtime observer without changing transport/session state."""
        self._diagnostics = diagnostics
        attach_modbus_logging()

    def _call_client(self, target, *args, **kwargs):
        with modbus_logging(self._diagnostics):
            return target(*args, **kwargs)

    @staticmethod
    def _error_reason(error, operation):
        if isinstance(error, ModbusTransportError):
            return error.reason
        if isinstance(error, TimeoutError):
            return "timeout"
        if isinstance(error, ModbusIOException):
            # Pinned pymodbus 3.13.1 wraps receive timeouts and identity violations
            # in the same class. Inspect only known markers; never log this text.
            message = error.string
            if "No response received" in message:
                return "timeout"
            if "ERROR: request uses" in message:
                return "response_identity_mismatch"
            return "invalid_response"
        if isinstance(error, ConnectionException):
            return "connection_lost"
        return f"{operation}_failed"

    @property
    def diagnostic_scope(self):
        return ("endpoint", id(self))

    def _event(self, event, **fields):
        if self._diagnostics is not None:
            self._diagnostics.event(
                1,
                "modbus",
                event,
                scope=self.diagnostic_scope,
                interval=REPEAT_SECONDS,
                connection_generation=self.generation,
                **fields,
            )

    def _failure(self, event, reason, *, device_id=None):
        if self._diagnostics is not None:
            scope = (
                ("device", device_id)
                if device_id is not None
                else self.diagnostic_scope
            )
            self._diagnostics.failure(
                scope,
                "modbus",
                event,
                reason=reason,
                connection_generation=self.generation,
                **({"device_id": device_id} if device_id else {}),
            )

    def _read_write_failure(self, reason, device_id):
        self._failure("communication_failure", reason, device_id=device_id)
        if reason == "response_identity_mismatch" and self._diagnostics is not None:
            self._diagnostics.event(
                0,
                "modbus",
                "protocol_error",
                reason=reason,
                scope=self.diagnostic_scope,
                interval=REPEAT_SECONDS,
                device_id=device_id,
            )
        self._reset_safely(reason)

    def connect(self) -> None:
        """Open the TCP connection or raise an integration-specific error."""
        if self._connected:
            return
        recovering = self._diagnostics is not None and self._diagnostics.active(
            self.diagnostic_scope
        )
        if recovering:
            self._event("connection_attempt", result="started")
        try:
            connected = self._call_client(self._client.connect)
        except Exception as err:
            self._failure("connection_failure", "connection_failed")
            self._reset_safely("connection_failed")
            raise ModbusConnectionError(
                "failed to connect to Modbus TCP device", reason="connection_failed"
            ) from err
        if not connected:
            self._failure("connection_failure", "connection_failed")
            self._reset_safely("connection_failed")
            raise ModbusConnectionError(
                "failed to connect to Modbus TCP device", reason="connection_failed"
            )
        self._connected = True
        self.generation += 1
        if recovering:
            self._event("connection_established", result="connected")

    def close(self) -> None:
        """Close the TCP connection once; repeated closure is harmless."""
        if not self._connected:
            return
        try:
            self._call_client(self._client.close)
        except Exception as err:
            raise ModbusTransportError("failed to close Modbus TCP connection") from err
        finally:
            self._connected = False
            self.generation += 1

    def reset(self) -> None:
        """Discard the current endpoint session without retrying a request."""
        connected = self._connected
        self.close()
        if connected:
            self._failure("session_reset", "explicit_reset")

    def bind(self, device_id: int = DEFAULT_UNIT_ID) -> ModbusDeviceTransport:
        """Return a lightweight request view bound to one Modbus device ID."""
        return ModbusDeviceTransport(self, device_id)

    def _ensure_connected(self) -> None:
        """Connect a new session for a later independent request if needed."""
        if self._connected and not self._client.connected:
            # Do not let pymodbus transparently reconnect a prepared request
            # into a session whose topology has not been validated.
            self._failure("connection_lost", "connection_lost")
            self._reset_safely("connection_lost")
            raise ModbusConnectionError(
                "Modbus session closed; revalidation required", reason="connection_lost"
            )
        if not self._connected:
            self.connect()

    def _reset_safely(self, reason="transport_error") -> None:
        """Reset without hiding the transport failure that triggered recovery."""
        self.generation += 1
        try:
            self._call_client(self._client.close)
        except Exception:
            pass
        finally:
            self._connected = False
        self._failure("session_reset", reason)

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
            response = self._call_client(
                self._client.read_holding_registers,
                address,
                count=count,
                device_id=device_id,
            )
        except Exception as err:
            reason = self._error_reason(err, "read")
            self._read_write_failure(reason, device_id)
            raise ModbusTransportError(
                "holding-register read failed", reason=reason
            ) from err

        if response.isError():
            self._read_write_failure("error_response", device_id)
            raise ModbusTransportError(
                "Modbus device returned an error response", reason="error_response"
            )
        try:
            registers = tuple(response.registers)
        except (AttributeError, TypeError) as err:
            self._read_write_failure("invalid_response", device_id)
            raise ModbusTransportError(
                "Modbus response contains no registers", reason="invalid_response"
            ) from err
        if len(registers) != count:
            self._read_write_failure("incomplete_response", device_id)
            raise ModbusTransportError(
                f"expected {count} registers but received {len(registers)}",
                reason="incomplete_response",
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
                response = self._call_client(
                    self._client.write_register, address, words[0], device_id=device_id
                )
            else:
                response = self._call_client(
                    self._client.write_registers,
                    address,
                    list(words),
                    device_id=device_id,
                )
        except Exception as err:
            reason = self._error_reason(err, "write")
            self._read_write_failure(reason, device_id)
            raise ModbusTransportError(
                "holding-register write failed", reason=reason
            ) from err
        if response.isError():
            self._read_write_failure("error_response", device_id)
            raise ModbusTransportError(
                "Modbus device returned an error response", reason="error_response"
            )


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

    def write_holding_registers(self, address: int, values: Sequence[int]) -> None:
        """Write through the shared endpoint with this view's device ID."""
        self.endpoint.write_holding_registers(address, values, device_id=self.device_id)


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

    def write_holding_registers(self, address: int, values: Sequence[int]) -> None:
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
