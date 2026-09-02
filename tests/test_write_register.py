"""Tests for the standalone safe SunSpec register write utility."""

from io import StringIO

import pytest

from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.sunspec import (
    SUNSPEC_BASE_TRANSPORT_ADDRESS,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tools.write_register import (
    create_argument_parser,
    execute_register_write,
    parse_requested_value,
    resolve_write_parameter,
)


def definition(model, name):
    """Find one fixed definition by name."""
    return next(register for register in model.registers if register.name == name)


def model_chain(model_id: int, length: int) -> dict[int, int]:
    """Create one synthetic discovered SunSpec model and end marker."""
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS
    model_base = base + 4
    registers = {
        base: 0x5375,
        base + 1: 0x6E53,
        base + 2: model_id,
        base + 3: length,
        model_base + length: 0xFFFF,
        model_base + length + 1: 0,
    }
    registers.update({model_base + offset: 0 for offset in range(length)})
    return registers


class FakeTransport:
    """Simulate discovery, reads, one write, and configurable failures."""

    def __init__(
        self,
        registers: dict[int, int],
        *,
        update_after_write: bool = True,
        write_error: bool = False,
        read_back_error: bool = False,
    ) -> None:
        self.registers = registers
        self.update_after_write = update_after_write
        self.write_error = write_error
        self.read_back_error = read_back_error
        self.connected = False
        self.closed = False
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.operations: list[str] = []

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        self.operations.append(f"read:{address}:{count}")
        self.reads.append((address, count))
        if self.read_back_error and self.writes and address == self.writes[0][0]:
            raise ModbusTransportError("read-back connection lost")
        return tuple(self.registers[address + offset] for offset in range(count))

    def write_holding_registers(self, address: int, values) -> None:
        self.operations.append(f"write:{address}")
        if self.write_error:
            raise ModbusTransportError("write rejected")
        words = tuple(values)
        self.writes.append((address, words))
        if self.update_after_write:
            for offset, word in enumerate(words):
                self.registers[address + offset] = word


def storage_transport(**kwargs) -> FakeTransport:
    """Build Model 124 data with a live -2 storage percentage scale factor."""
    registers = model_chain(124, 24)
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    registers[base + definition(MODEL_124, "MinRsvPct_SF").offset] = 0xFFFE
    registers[base + definition(MODEL_124, "MinRsvPct").offset] = 700
    registers[base + definition(MODEL_124, "InOutWRte_SF").offset] = 0xFFFE
    registers[base + definition(MODEL_124, "OutWRte").offset] = 0
    return FakeTransport(registers, **kwargs)


def execute(
    transport: FakeTransport,
    *,
    parameter: str = "124:MinRsvPct",
    value: str = "10",
    write: bool = False,
    yes: bool = False,
    language: str = "en",
    confirmation: str = "NO",
) -> tuple[int, str, str]:
    """Run the write utility with captured streams and injected dependencies."""
    stdout = StringIO()
    stderr = StringIO()
    status = execute_register_write(
        "192.0.2.20",
        502,
        1,
        parameter,
        value,
        write=write,
        yes=yes,
        language=language,
        transport_factory=lambda *args, **kwargs: transport,
        input_function=lambda prompt: confirmation,
        stdout=stdout,
        stderr=stderr,
    )
    return status, stdout.getvalue(), stderr.getvalue()


def test_parameter_resolution_requires_qualified_fixed_writable_register() -> None:
    """Metadata lookup admits only one known fixed READ_WRITE definition."""
    assert resolve_write_parameter("124:MinRsvPct").register.name == "MinRsvPct"
    for parameter, message in (
        ("MinRsvPct", "MODEL_ID:NAME"),
        ("999:W", "unknown model"),
        ("124:Missing", "unknown register"),
        ("124:ChaState", "read-only"),
        ("160:DCW", "repeating-block"),
    ):
        with pytest.raises(ValueError, match=message):
            resolve_write_parameter(parameter)


def test_cli_requires_device_id() -> None:
    """The write tool requires an explicit target Modbus device ID."""
    parser = create_argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--host",
                "192.0.2.20",
                "--parameter",
                "124:MinRsvPct",
                "--value",
                "10",
            ]
        )


def test_enum_input_accepts_numeric_value_and_exact_label() -> None:
    """Enum parsing supports canonical numbers and unambiguous exact labels."""
    register = definition(MODEL_124, "ChaGriSet")
    assert parse_requested_value(register, "1") == 1
    assert parse_requested_value(register, "GRID (Charging from grid enabled)") == 1
    with pytest.raises(ValueError, match="enum"):
        parse_requested_value(register, "grid")


def test_missing_discovered_model_is_rejected_and_transport_closed() -> None:
    """A known definition cannot be written unless the live model is present."""
    transport = FakeTransport(model_chain(103, 50))
    status, _, stderr = execute(transport)
    assert status != 0
    assert "model 124 is not present" in stderr
    assert not transport.writes
    assert transport.closed


def test_dry_run_reads_scale_and_current_but_never_writes() -> None:
    """Default execution performs full live validation without modification."""
    transport = storage_transport()
    status, stdout, stderr = execute(transport)
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4

    assert status == 0
    assert stderr == ""
    assert "Current value:   7 % WChaMax" in stdout
    assert "Requested value: 10 % WChaMax" in stdout
    assert "Writing raw:     0x03E8" in stdout
    assert "Scale factor: -2" in stdout
    assert "DRY RUN - no register was written." in stdout
    assert (base + definition(MODEL_124, "MinRsvPct_SF").offset, 1) in transport.reads
    assert (base + definition(MODEL_124, "MinRsvPct").offset, 1) in transport.reads
    assert not transport.writes


def test_yes_without_write_remains_dry_run() -> None:
    """Confirmation bypass cannot independently authorize a write."""
    transport = storage_transport()
    status, stdout, _ = execute(transport, yes=True)
    assert status == 0
    assert "DRY RUN" in stdout
    assert not transport.writes


def test_write_requires_exact_interactive_confirmation() -> None:
    """Any interactive response other than exact YES aborts safely."""
    transport = storage_transport()
    status, stdout, _ = execute(transport, write=True, confirmation="yes")
    assert status == 0
    assert "Write aborted" in stdout
    assert not transport.writes


def test_exact_interactive_confirmation_performs_write() -> None:
    """Exact YES authorizes the same single verified write as --yes."""
    transport = storage_transport()
    status, stdout, _ = execute(transport, write=True, confirmation="YES")
    assert status == 0
    assert len(transport.writes) == 1
    assert "SUCCESS" in stdout


def test_write_yes_performs_one_write_after_read_and_successful_read_back() -> None:
    """Explicit authorization writes once and verifies decoded semantics."""
    transport = storage_transport()
    status, stdout, stderr = execute(transport, write=True, yes=True)
    target = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4 + definition(
        MODEL_124, "MinRsvPct"
    ).offset

    assert status == 0
    assert stderr == ""
    assert transport.writes == [(target, (1000,))]
    write_index = transport.operations.index(f"write:{target}")
    assert any(
        operation.startswith("read:")
        for operation in transport.operations[:write_index]
    )
    assert transport.operations[write_index + 1] == f"read:{target}:1"
    assert "Read-back value: 10 % WChaMax" in stdout
    assert "SUCCESS: read-back matches requested value." in stdout


def test_semantic_read_back_mismatch_returns_failure() -> None:
    """A successful protocol write is insufficient when semantics do not change."""
    transport = storage_transport(update_after_write=False)
    status, _, stderr = execute(transport, write=True, yes=True)
    assert status != 0
    assert "FAILED: read-back does not match" in stderr


def test_write_failure_returns_nonzero_without_read_back() -> None:
    """A transport write failure is concise and stops verification."""
    transport = storage_transport(write_error=True)
    status, _, stderr = execute(transport, write=True, yes=True)
    assert status != 0
    assert "write rejected" in stderr
    assert not transport.writes


def test_read_back_failure_is_reported_separately() -> None:
    """A post-write read failure is distinguished from the completed write."""
    transport = storage_transport(read_back_error=True)
    status, _, stderr = execute(transport, write=True, yes=True)
    assert status != 0
    assert "write succeeded but read-back failed" in stderr
    assert len(transport.writes) == 1


@pytest.mark.parametrize(
    ("parameter", "value", "message"),
    [
        ("124:OutWRte", "101", "above"),
        ("124:VAChaMax", "65535", "sentinel"),
    ],
)
def test_encoder_validation_failure_occurs_before_write(
    parameter: str, value: str, message: str
) -> None:
    """Range and sentinel safety cannot be bypassed by the live tool."""
    transport = storage_transport()
    status, _, stderr = execute(transport, parameter=parameter, value=value)
    assert status != 0
    assert message in stderr
    assert not transport.writes


def test_unavailable_live_scale_factor_is_rejected_before_write() -> None:
    """A live invalid SUNSSF cannot be replaced by a documented assumption."""
    transport = storage_transport()
    base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    scale = definition(MODEL_124, "MinRsvPct_SF")
    transport.registers[base + scale.offset] = 0x8000

    status, _, stderr = execute(transport, write=True, yes=True)

    assert status != 0
    assert "scale factor 'MinRsvPct_SF' is unavailable or invalid" in stderr
    assert not transport.writes


def test_enum_write_uses_canonical_semantic_read_back() -> None:
    """Enum verification compares its decoded canonical numeric selection."""
    transport = storage_transport()
    status, stdout, _ = execute(
        transport,
        parameter="124:ChaGriSet",
        value="GRID (Charging from grid enabled)",
        write=True,
        yes=True,
    )
    assert status == 0
    assert "Read-back value: GRID (Charging from grid enabled)" in stdout
    assert "SUCCESS" in stdout


def test_localized_help_and_english_fallback() -> None:
    """Write summaries use the shared localized help-text fallback helper."""
    german = storage_transport()
    _, german_stdout, _ = execute(
        german, parameter="124:ChaGriSet", value="1", language="de"
    )
    fallback = storage_transport()
    _, fallback_stdout, _ = execute(
        fallback, parameter="124:ChaGriSet", value="1", language="fr"
    )
    assert "Legt fest, ob das Laden aus dem Netz" in german_stdout
    assert "Selects whether charging from the grid is permitted" in fallback_stdout
