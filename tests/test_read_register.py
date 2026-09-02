"""Tests for the standalone read-only semantic register utility."""

from io import StringIO

import pytest

from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.sunspec import (
    SUNSPEC_BASE_TRANSPORT_ADDRESS,
)
from tests.test_write_register import (
    FakeTransport,
    definition,
    model_chain,
    storage_transport,
)
from tools.read_register import (
    create_argument_parser,
    execute_register_read,
    resolve_read_parameter,
)


def execute(
    transport: FakeTransport,
    parameter: str,
    *,
    language: str = "en",
) -> tuple[int, str, str]:
    """Run one injected read and capture both output streams."""
    stdout = StringIO()
    stderr = StringIO()
    status = execute_register_read(
        "192.0.2.20",
        502,
        1,
        parameter,
        language=language,
        transport_factory=lambda *args, **kwargs: transport,
        stdout=stdout,
        stderr=stderr,
    )
    return status, stdout.getvalue(), stderr.getvalue()


def set_storage_value(transport: FakeTransport, name: str, value: int) -> None:
    """Set one raw Model 124 payload word."""
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    transport.registers[base + definition(MODEL_124, name).offset] = value


def test_scaled_numeric_register_uses_live_scale_factor() -> None:
    """A scaled register is decoded through the existing SunSpec codec."""
    transport = storage_transport()

    status, stdout, stderr = execute(transport, "124:MinRsvPct")

    assert status == 0
    assert stderr == ""
    assert "Device ID: 1" in stdout
    assert "Model: 124 - Storage" in stdout
    assert "Parameter: MinRsvPct" in stdout
    assert "Access: read_write" in stdout
    assert "Current value: 7 % WChaMax" in stdout
    assert "Raw value: 700 (0x02BC)" in stdout
    assert "Scale factor: -2" in stdout
    assert transport.writes == []
    assert transport.closed


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (0, "PV (Charging from grid disabled)"),
        (1, "GRID (Charging from grid enabled)"),
    ],
)
def test_enum_register_shows_raw_value_and_documented_label(
    raw: int, label: str
) -> None:
    """ENUM16 output retains both authoritative semantics and numeric raw data."""
    transport = storage_transport()
    set_storage_value(transport, "ChaGriSet", raw)

    status, stdout, stderr = execute(transport, "124:ChaGriSet")

    assert status == 0
    assert stderr == ""
    assert f"Current value: {label}" in stdout
    assert f"Raw value: {raw} (0x{raw:04X})" in stdout
    assert transport.writes == []


def test_read_only_register_is_supported() -> None:
    """Semantic reads do not require READ_WRITE access."""
    transport = storage_transport()
    set_storage_value(transport, "ChaState", 550)
    set_storage_value(transport, "ChaState_SF", 0xFFFF)

    status, stdout, stderr = execute(transport, "124:ChaState")

    assert status == 0
    assert stderr == ""
    assert "Access: read" in stdout
    assert "Current value: 55 % AhrRtg" in stdout
    assert "Raw value: 550 (0x0226)" in stdout
    assert "Scale factor: -1" in stdout
    assert transport.writes == []


def test_unknown_and_malformed_parameters_fail_before_connection() -> None:
    """Invalid semantic coordinates are rejected without device I/O."""
    for parameter, message in (
        ("MinRsvPct", "MODEL_ID:NAME"),
        ("invalid:MinRsvPct", "invalid model ID"),
        ("124:Missing", "unknown register"),
    ):
        transport = storage_transport()
        status, _, stderr = execute(transport, parameter)
        assert status != 0
        assert message in stderr
        assert not transport.connected
        assert transport.writes == []


def test_missing_discovered_model_is_reported_and_transport_closed() -> None:
    """A known register requires its model in the discovered live chain."""
    transport = FakeTransport(model_chain(103, 50))

    status, _, stderr = execute(transport, "124:MinRsvPct")

    assert status != 0
    assert "model 124 is not present" in stderr
    assert transport.closed
    assert transport.writes == []


def test_localized_help_uses_existing_fallback_helper() -> None:
    """The language option affects project help text without translating enums."""
    transport = storage_transport()
    set_storage_value(transport, "ChaGriSet", 1)

    status, stdout, _ = execute(transport, "124:ChaGriSet", language="de")

    assert status == 0
    assert "GRID (Charging from grid enabled)" in stdout
    assert "Legt fest, ob das Laden aus dem Netz" in stdout


def test_cli_has_no_write_or_confirmation_options() -> None:
    """The read utility exposes no command-line path capable of writing."""
    parser = create_argument_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }

    assert "--write" not in option_strings
    assert "--yes" not in option_strings
    assert "--value" not in option_strings
    assert resolve_read_parameter("124:ChaGriSet").register.name == "ChaGriSet"
