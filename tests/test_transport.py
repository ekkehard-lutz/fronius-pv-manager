"""Tests for the Home Assistant-independent Modbus TCP transport."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.fronius_pv_manager import transport as transport_module
from custom_components.fronius_pv_manager.transport import (
    ModbusConnectionError,
    ModbusTcpEndpointTransport,
    ModbusTcpTransport,
    ModbusTransportError,
    read_holding_registers_chunked,
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


def create_endpoint(monkeypatch: pytest.MonkeyPatch, client: Mock):
    """Create one endpoint and two device views backed by one mock client."""
    client.connect.return_value = True
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(transport_module, "ModbusTcpClient", client_factory)
    endpoint = ModbusTcpEndpointTransport("inverter.local", port=1502)
    client_factory.assert_called_once_with(
        "inverter.local", port=1502, retries=0, reconnect_delay=0
    )
    return endpoint, endpoint.bind(1), endpoint.bind(200)


def test_bound_views_share_one_endpoint_and_forward_device_ids(monkeypatch) -> None:
    """Two device contexts share one client and retain request unit metadata."""
    response = SimpleNamespace(isError=lambda: False, registers=[7])
    client = Mock()
    client.read_holding_registers.return_value = response
    endpoint, inverter, meter = create_endpoint(monkeypatch, client)

    assert inverter.endpoint is endpoint
    assert meter.endpoint is endpoint
    assert inverter.read_holding_registers(40000, 1) == (7,)
    assert meter.read_holding_registers(40001, 1) == (7,)
    assert client.read_holding_registers.call_args_list == [
        ((40000,), {"count": 1, "device_id": 1}),
        ((40001,), {"count": 1, "device_id": 200}),
    ]
    client.connect.assert_called_once_with()


def test_chunked_reads_keep_bound_device_id(monkeypatch) -> None:
    """Every chunk of a large payload retains the view's device ID."""
    client = Mock()
    client.read_holding_registers.side_effect = [
        SimpleNamespace(isError=lambda: False, registers=list(range(100))),
        SimpleNamespace(isError=lambda: False, registers=list(range(100, 105))),
    ]
    _, _, meter = create_endpoint(monkeypatch, client)

    assert read_holding_registers_chunked(meter, 40100, 105) == tuple(range(105))
    assert client.read_holding_registers.call_args_list == [
        ((40100,), {"count": 100, "device_id": 200}),
        ((40200,), {"count": 5, "device_id": 200}),
    ]


def test_read_failure_resets_without_retry_and_next_request_reconnects(
    monkeypatch,
) -> None:
    """A failed read closes its session; a later device request starts a new one."""
    response = SimpleNamespace(isError=lambda: False, registers=[9])
    client = Mock()
    client.read_holding_registers.side_effect = [OSError("timeout"), response]
    _, inverter, meter = create_endpoint(monkeypatch, client)

    with pytest.raises(ModbusTransportError):
        inverter.read_holding_registers(40000, 1)

    assert client.read_holding_registers.call_count == 1
    client.close.assert_called_once_with()
    assert meter.read_holding_registers(40000, 1) == (9,)
    assert client.connect.call_count == 2


def test_write_failure_resets_and_writes_exactly_once(monkeypatch) -> None:
    """An uncertain write closes the endpoint and is never repeated."""
    client = Mock()
    client.write_register.side_effect = OSError("response lost")
    _, inverter, _ = create_endpoint(monkeypatch, client)

    with pytest.raises(ModbusTransportError):
        inverter.write_holding_registers(40100, (1,))

    client.write_register.assert_called_once_with(40100, 1, device_id=1)
    client.close.assert_called_once_with()


def test_endpoint_close_is_idempotent(monkeypatch) -> None:
    """Only one public client close occurs for repeated endpoint closure."""
    client = Mock()
    endpoint, _, _ = create_endpoint(monkeypatch, client)
    endpoint.connect()

    endpoint.close()
    endpoint.close()

    client.close.assert_called_once_with()


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
    client.connect.return_value = True
    transport = create_transport(monkeypatch, client)

    transport.connect()
    transport.close()
    transport.close()

    client.close.assert_called_once_with()


def test_client_close_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client close failure is retained as the transport error cause."""
    failure = OSError("close failed")
    client = Mock()
    client.connect.return_value = True
    client.close.side_effect = failure
    transport = create_transport(monkeypatch, client)

    transport.connect()
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


def test_single_register_write_uses_public_client_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One word uses write_register with the configured device ID."""
    response = SimpleNamespace(isError=lambda: False)
    client = Mock()
    client.write_register.return_value = response
    transport = create_transport(monkeypatch, client)

    transport.write_holding_registers(40100, (0x1234,))

    client.write_register.assert_called_once_with(40100, 0x1234, device_id=7)
    client.write_registers.assert_not_called()


def test_multiple_register_write_uses_public_client_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple words use write_registers in existing big-endian order."""
    response = SimpleNamespace(isError=lambda: False)
    client = Mock()
    client.write_registers.return_value = response
    transport = create_transport(monkeypatch, client)

    transport.write_holding_registers(40100, (0x1234, 0x5678))

    client.write_registers.assert_called_once_with(
        40100, [0x1234, 0x5678], device_id=7
    )
    client.write_register.assert_not_called()


@pytest.mark.parametrize(
    ("address", "values"),
    [(-1, (1,)), (0, ()), (0, (-1,)), (0, (0x10000,)), (0, (True,))],
)
def test_invalid_register_writes_are_rejected(address, values) -> None:
    """Transport coordinates and raw words are validated before client access."""
    transport = object.__new__(ModbusTcpEndpointTransport)
    with pytest.raises(ValueError):
        transport.write_holding_registers(address, values, device_id=1)


def test_write_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pymodbus write exception is retained as the transport error cause."""
    failure = OSError("connection lost")
    client = Mock()
    client.write_register.side_effect = failure
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError) as raised:
        transport.write_holding_registers(40100, (1,))

    assert raised.value.__cause__ is failure


def test_write_error_response_becomes_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pymodbus write error response stays inside the transport boundary."""
    response = SimpleNamespace(isError=lambda: True)
    client = Mock()
    client.write_register.return_value = response
    transport = create_transport(monkeypatch, client)

    with pytest.raises(ModbusTransportError, match="error response"):
        transport.write_holding_registers(40100, (1,))
