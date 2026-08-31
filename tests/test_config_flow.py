"""Tests for the Fronius PV Manager config flow."""

from types import SimpleNamespace

import pytest
from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT

import custom_components.fronius_pv_manager.config_flow as config_flow_module
from custom_components.fronius_pv_manager.config_flow import (
    FroniusPVManagerConfigFlow,
    parse_device_ids,
)
from custom_components.fronius_pv_manager.const import (
    CONF_DEVICE_IDS,
    DEFAULT_PORT,
    DOMAIN,
)
from tests.runtime_fakes import FakeHass, FakeTransport, model_chain


def _flow(hass: FakeHass | None = None) -> FroniusPVManagerConfigFlow:
    """Create a directly testable real Home Assistant config flow."""
    flow = FroniusPVManagerConfigFlow()
    flow.hass = hass or FakeHass()
    flow.handler = DOMAIN
    flow.context = {"source": SOURCE_USER}
    flow.flow_id = "test-flow"
    return flow


def _valid_input(**changes) -> dict[str, object]:
    """Return representative user input with optional replacements."""
    data = {
        CONF_HOST: "192.0.2.40",
        CONF_PORT: DEFAULT_PORT,
        CONF_DEVICE_IDS: "1",
    }
    data.update(changes)
    return data


def _install_transport_factory(monkeypatch, transports: dict[int, FakeTransport]):
    """Replace transport construction and record generic endpoint arguments."""
    calls = []

    def factory(host, *, port, device_id):
        calls.append((host, port, device_id))
        return transports[device_id]

    monkeypatch.setattr(config_flow_module, "ModbusTcpTransport", factory)
    return calls


@pytest.mark.asyncio
async def test_user_step_shows_form_with_default_port() -> None:
    """The initial user step exposes all fields and defaults TCP port 502."""
    result = await _flow().async_step_user()

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    schema = result["data_schema"].schema
    port_marker = next(marker for marker in schema if marker.schema == CONF_PORT)
    assert port_marker.default() == 502


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", (1,)),
        ("1,200", (1, 200)),
        (" 1, 200, 201 ", (1, 200, 201)),
    ],
)
def test_device_id_parser_preserves_normalized_order(value, expected) -> None:
    """Valid comma-separated IDs retain the explicit user order."""
    assert parse_device_ids(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "   ", "one", "1,,200", "1,", "0", "248", "-1"],
)
def test_invalid_device_id_lists_are_rejected(value) -> None:
    """Empty, malformed, nonnumeric, and out-of-range lists fail clearly."""
    with pytest.raises(ValueError):
        parse_device_ids(value)


def test_duplicate_device_ids_are_rejected() -> None:
    """Duplicates are errors rather than being silently removed."""
    with pytest.raises(ValueError, match="unique"):
        parse_device_ids("1, 200, 1")


@pytest.mark.asyncio
async def test_valid_single_device_creates_normalized_entry(monkeypatch) -> None:
    """A successful flow stores only generic endpoint configuration."""
    registers, _ = model_chain((103, 50))
    transport = FakeTransport(registers)
    calls = _install_transport_factory(monkeypatch, {1: transport})
    flow = _flow()

    result = await flow.async_step_user(
        _valid_input(**{CONF_HOST: "  EXAMPLE.local.  "})
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "EXAMPLE.local."
    assert result["data"] == {
        CONF_HOST: "EXAMPLE.local.",
        CONF_PORT: 502,
        CONF_DEVICE_IDS: [1],
    }
    assert flow.unique_id == "example.local:502"
    assert calls == [("EXAMPLE.local.", 502, 1)]
    assert transport.close_calls == 1
    assert [job.__name__ for job in flow.hass.executor_jobs] == [
        "_validate_endpoint"
    ]


@pytest.mark.asyncio
async def test_valid_multiple_devices_all_validate_and_close(monkeypatch) -> None:
    """Every configured participant is validated, closed, and stored in order."""
    registers, _ = model_chain((1, 65))
    transports = {1: FakeTransport(registers), 200: FakeTransport(registers)}
    calls = _install_transport_factory(monkeypatch, transports)

    result = await _flow().async_step_user(
        _valid_input(**{CONF_DEVICE_IDS: " 200, 1 "})
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_IDS] == [200, 1]
    assert calls == [
        ("192.0.2.40", 502, 200),
        ("192.0.2.40", 502, 1),
    ]
    assert all(transport.connect_calls == 1 for transport in transports.values())
    assert all(transport.close_calls == 1 for transport in transports.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_ids", "error"),
    [
        ("x", "invalid_device_ids"),
        ("", "invalid_device_ids"),
        ("1,,2", "invalid_device_ids"),
        ("248", "invalid_device_ids"),
        ("1,1", "duplicate_device_ids"),
    ],
)
async def test_device_id_validation_errors_are_returned(device_ids, error) -> None:
    """Device-list errors stay localized field errors without network access."""
    flow = _flow()

    result = await flow.async_step_user(
        _valid_input(**{CONF_DEVICE_IDS: device_ids})
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {CONF_DEVICE_IDS: error}
    assert flow.hass.executor_jobs == []


@pytest.mark.asyncio
async def test_whitespace_only_host_is_rejected_without_network_access() -> None:
    """Trimming cannot turn a required host into an empty endpoint."""
    flow = _flow()

    result = await flow.async_step_user(_valid_input(**{CONF_HOST: "   "}))

    assert result["errors"] == {CONF_HOST: "invalid_host"}
    assert flow.hass.executor_jobs == []


@pytest.mark.asyncio
async def test_connection_failure_closes_every_created_transport(monkeypatch) -> None:
    """A failed requested ID rejects the entry and closes all validation clients."""
    registers, _ = model_chain((1, 65))
    transports = {
        1: FakeTransport(registers, connection_error=True),
        200: FakeTransport(registers),
    }
    _install_transport_factory(monkeypatch, transports)

    result = await _flow().async_step_user(
        _valid_input(**{CONF_DEVICE_IDS: "1, 200"})
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert all(transport.close_calls == 1 for transport in transports.values())


@pytest.mark.asyncio
async def test_invalid_sunspec_response_has_specific_error(monkeypatch) -> None:
    """A reachable non-SunSpec participant reports the discovery error."""
    transport = FakeTransport({40000: 0, 40001: 0})
    _install_transport_factory(monkeypatch, {1: transport})

    result = await _flow().async_step_user(_valid_input())

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_sunspec"}
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_unexpected_validation_failure_is_hidden_and_closes_created_clients(
    monkeypatch,
) -> None:
    """Programming failures use the generic UI error without leaking details."""
    registers, _ = model_chain((1, 65))
    first = FakeTransport(registers)

    def factory(host, *, port, device_id):
        if device_id == 200:
            raise RuntimeError("private failure details")
        return first

    monkeypatch.setattr(config_flow_module, "ModbusTcpTransport", factory)

    result = await _flow().async_step_user(
        _valid_input(**{CONF_DEVICE_IDS: "1, 200"})
    )

    assert result["errors"] == {"base": "unknown"}
    assert first.close_calls == 1


@pytest.mark.asyncio
async def test_duplicate_endpoint_aborts_before_validation() -> None:
    """Endpoint identity ignores device IDs and normalizes hostname casing/dot."""
    hass = FakeHass()
    hass.config_entries.entries_by_unique_id[(DOMAIN, "example.local:502")] = (
        SimpleNamespace(source=SOURCE_USER)
    )
    flow = _flow(hass)

    with pytest.raises(data_entry_flow.AbortFlow) as raised:
        await flow.async_step_user(
            _valid_input(
                **{
                    CONF_HOST: " Example.Local. ",
                    CONF_DEVICE_IDS: "200, 201",
                }
            )
        )

    assert raised.value.reason == "already_configured"
    assert hass.executor_jobs == []
