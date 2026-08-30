"""Tests for the Home Assistant-independent Modbus TCP transport."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.fronius_pv_manager import transport as transport_module
from custom_components.fronius_pv_manager.transport import (
    ModbusConnectionError,
    ModbusTcpTransport,
    ModbusTransportError,
)


def create_transport(
    monkeypatch: pytest.MonkeyPatch, client: Mock
) -> ModbusTcpTransport:
    """Create a transport backed by a mock pymodbus client."""
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(transport_module, "ModbusTcpClient", client_factory)
    transport = ModbusTcpTransport("inverter.local", port=1502, device_id=7)
    client_factory.assert_called_once_with(
        "inverter.local", port=1502, retries=0, reconnect_delay=0
    )
    return transport


def test_successful_transport_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A truthy pymodbus connection result completes normally."""
    client = Mock()
    client.connect.return_value = True
    transport = create_transport(monkeypatch, client)

    transport.connect()

    client.connect.assert_called_once_with()


def test_failed_transport_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A false pymodbus connection result becomes a connection error."""
    client = Mock()
    client.connect.return_value = False
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusConnectionError):
        transport.connect()


def test_transport_close_calls_client_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the transport delegates to the public pymodbus close method."""
    client = Mock()
    transport = create_transport(monkeypatch, client)

    transport.close()

    client.close.assert_called_once_with()


def test_client_close_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client close failure is retained as the transport error cause."""
    failure = OSError("close failed")
    client = Mock()
    client.close.side_effect = failure
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError) as raised:
        transport.close()

    assert raised.value.__cause__ is failure


def test_successful_holding_register_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful response is reduced to immutable register values."""
    response = SimpleNamespace(isError=lambda: False, registers=[1, 2])
    client = Mock()
    client.read_holding_registers.return_value = response
    transport = create_transport(monkeypatch, client)

    registers = transport.read_holding_registers(40000, 2)

    assert registers == (1, 2)
    client.read_holding_registers.assert_called_once_with(
        40000, count=2, device_id=7
    )


def test_short_holding_register_response_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response with fewer registers than requested is a transport error."""
    response = SimpleNamespace(isError=lambda: False, registers=[1])
    client = Mock()
    client.read_holding_registers.return_value = response
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError, match="expected 2"):
        transport.read_holding_registers(40000, 2)


def test_error_response_becomes_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pymodbus error response does not escape the transport boundary."""
    response = SimpleNamespace(isError=lambda: True)
    client = Mock()
    client.read_holding_registers.return_value = response
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError, match="error response"):
        transport.read_holding_registers(40000, 2)


def test_client_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary client exception is retained as the transport error cause."""
    failure = OSError("connection lost")
    client = Mock()
    client.read_holding_registers.side_effect = failure
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError) as raised:
        transport.read_holding_registers(40000, 2)

    assert raised.value.__cause__ is failure
