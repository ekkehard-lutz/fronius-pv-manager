"""Tests for the developer-only SunSpec device inspector."""

from io import StringIO

import pytest

from custom_components.fronius_pv_manager.models import (
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
)
from custom_components.fronius_pv_manager.register_maps import (
    MODEL_103,
    MODEL_160,
    MODEL_203,
)
from custom_components.fronius_pv_manager.sunspec import (
    SUNSPEC_BASE_TRANSPORT_ADDRESS,
)
from custom_components.fronius_pv_manager.transport import (
    ModbusConnectionError,
)
from tools.inspect_device import _format_value, create_argument_parser, inspect_device


class FakeTransport:
    """Provide a synthetic SunSpec chain while tracking lifecycle calls."""

    def __init__(
        self,
        registers: dict[int, int],
        *,
        connection_error: ModbusConnectionError | None = None,
    ) -> None:
        """Initialize register data and optional connection failure."""
        self.registers = registers
        self.connection_error = connection_error
        self.connect_called = False
        self.close_called = False
        self.reads: list[tuple[int, int]] = []

    def connect(self) -> None:
        """Record connection and optionally raise the configured failure."""
        self.connect_called = True
        if self.connection_error is not None:
            raise self.connection_error

    def close(self) -> None:
        """Record transport closure."""
        self.close_called = True

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read a contiguous range from the synthetic register map."""
        self.reads.append((address, count))
        return tuple(self.registers[address + offset] for offset in range(count))


def model_chain(*models: tuple[int, int]) -> dict[int, int]:
    """Build a synthetic SunSpec model chain at the default transport base."""
    registers = {
        SUNSPEC_BASE_TRANSPORT_ADDRESS: 0x5375,
        SUNSPEC_BASE_TRANSPORT_ADDRESS + 1: 0x6E53,
    }
    header_address = SUNSPEC_BASE_TRANSPORT_ADDRESS + 2
    for model_id, length in models:
        registers[header_address] = model_id
        registers[header_address + 1] = length
        for offset in range(length):
            registers[header_address + 2 + offset] = offset
        header_address += 2 + length
    registers[header_address] = 0xFFFF
    registers[header_address + 1] = 0
    return registers


def run_inspection(
    transport: FakeTransport,
    *,
    dump_model: int | None = None,
    decode: bool = False,
) -> tuple[int, str, str]:
    """Run inspection with captured streams and an injected fake transport."""
    stdout = StringIO()
    stderr = StringIO()

    status = inspect_device(
        "192.0.2.10",
        502,
        1,
        dump_model=dump_model,
        decode=decode,
        transport_factory=lambda *args, **kwargs: transport,
        stdout=stdout,
        stderr=stderr,
    )
    return status, stdout.getvalue(), stderr.getvalue()


def set_payload(registers: dict[int, int], payload: list[int]) -> None:
    """Replace the first discovered model payload in a synthetic chain."""
    model_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    registers.update(
        {model_base + offset: value for offset, value in enumerate(payload)}
    )


def string_words(value: str, register_count: int) -> list[int]:
    """Encode an ASCII string as SunSpec big-endian register words."""
    data = value.encode("ascii").ljust(register_count * 2, b"\0")
    return [
        int.from_bytes(data[index : index + 2], "big")
        for index in range(0, len(data), 2)
    ]


def decodable_payload(definition) -> list[int]:
    """Create neutral words with valid scale factors for one definition."""
    payload = [0] * definition.expected_length
    for register in definition.registers:
        if register.data_type is RegisterDataType.SUNSSF:
            payload[register.offset] = 0
    return payload


def bitfield_definition(data_type: RegisterDataType) -> RegisterDefinition:
    """Create one compact bitfield definition for presentation tests."""
    return RegisterDefinition(
        name="events",
        offset=0,
        size=1 if data_type is RegisterDataType.BITFIELD16 else 2,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        bitfield={0x0001: "ready", 0x0002: "warning"},
    )


@pytest.mark.parametrize(
    "data_type", [RegisterDataType.BITFIELD16, RegisterDataType.BITFIELD32]
)
def test_valid_zero_bitfield_is_displayed_as_none(
    data_type: RegisterDataType,
) -> None:
    """A valid bitfield without active mapped bits is explicitly empty."""
    assert _format_value(
        bitfield_definition(data_type), RegisterValue(raw=0, value="")
    ) == "none"


@pytest.mark.parametrize(
    "data_type", [RegisterDataType.BITFIELD16, RegisterDataType.BITFIELD32]
)
def test_invalid_bitfield_is_displayed_as_unavailable(
    data_type: RegisterDataType,
) -> None:
    """An invalid bitfield retains the common unavailable presentation."""
    assert _format_value(
        bitfield_definition(data_type), RegisterValue(raw=0xFFFF, value=None)
    ) == "unavailable"


@pytest.mark.parametrize(
    "data_type", [RegisterDataType.BITFIELD16, RegisterDataType.BITFIELD32]
)
def test_active_bitfield_keeps_existing_labels(data_type: RegisterDataType) -> None:
    """Active bitfield labels remain comma-separated and unchanged."""
    assert _format_value(
        bitfield_definition(data_type),
        RegisterValue(raw=3, value="ready, warning"),
    ) == "ready, warning"


def test_successful_output_for_multiple_models_and_closes_transport() -> None:
    """A successful inspection summarizes every model and closes transport."""
    transport = FakeTransport(model_chain((1, 4), (103, 6)))

    status, stdout, stderr = run_inspection(transport)

    assert status == 0
    assert "Host: 192.0.2.10" in stdout
    assert "Connection: successful" in stdout
    assert "SunSpec signature: valid" in stdout
    assert "Model 1: length=4" in stdout
    assert "Model 103: length=6" in stdout
    assert "Discovered model count: 2" in stdout
    assert "raw payload" not in stdout
    assert stderr == ""
    assert transport.close_called


def test_output_documentation_address_is_transport_address_plus_one() -> None:
    """The summary labels both address forms using the documented conversion."""
    transport = FakeTransport(model_chain((1, 4)))

    status, stdout, _ = run_inspection(transport)

    assert status == 0
    assert (
        "zero-based transport data address=40004, "
        "one-based documentation register=40005"
    ) in stdout


def test_transport_is_closed_after_discovery_failure() -> None:
    """An invalid SunSpec signature still results in transport closure."""
    registers = model_chain()
    registers[SUNSPEC_BASE_TRANSPORT_ADDRESS] = 0
    transport = FakeTransport(registers)

    status, _, stderr = run_inspection(transport)

    assert status != 0
    assert "invalid SunSpec signature" in stderr
    assert "Traceback" not in stderr
    assert transport.close_called


def test_transport_error_returns_failure_without_traceback() -> None:
    """An expected connection failure is concise and closes the transport."""
    transport = FakeTransport(
        model_chain(), connection_error=ModbusConnectionError("connection refused")
    )

    status, _, stderr = run_inspection(transport)

    assert status != 0
    assert "connection refused" in stderr
    assert "Traceback" not in stderr
    assert transport.close_called


def test_dump_discovered_model_prints_raw_register_representations() -> None:
    """A selected model is dumped with offsets, addresses, decimal, and hex."""
    registers = model_chain((160, 2))
    model_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    registers[model_base] = 0xFFFF
    registers[model_base + 1] = 0
    transport = FakeTransport(registers)

    status, stdout, stderr = run_inspection(transport, dump_model=160)

    assert status == 0
    assert "Model 160 raw payload:" in stdout
    assert "Offset  Transport  Register  Decimal  Hex" in stdout
    assert "0      40004     40005    65535  0xFFFF" in stdout
    assert "1      40005     40006        0  0x0000" in stdout
    assert (model_base, 2) in transport.reads
    assert stderr == ""
    assert transport.close_called


def test_requested_model_not_found_returns_failure_and_closes() -> None:
    """A missing requested model is reported without a traceback."""
    transport = FakeTransport(model_chain((1, 4)))

    status, stdout, stderr = run_inspection(transport, dump_model=160)

    assert status != 0
    assert "Discovered model count: 1" in stdout
    assert "model 160 was not found" in stderr
    assert "Traceback" not in stderr
    assert transport.close_called


def test_dump_reads_payload_of_88_registers_exactly() -> None:
    """A payload below the safe limit is read in one exact request."""
    transport = FakeTransport(model_chain((160, 88)))
    model_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4

    status, stdout, _ = run_inspection(transport, dump_model=160)

    assert status == 0
    assert transport.reads.count((model_base, 88)) == 1
    assert "    87      40091     40092       87  0x0057" in stdout


def test_dump_splits_105_register_payload_and_preserves_order() -> None:
    """A long payload is read as 100 plus 5 ordered registers."""
    transport = FakeTransport(model_chain((203, 105)))
    model_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4

    status, stdout, _ = run_inspection(transport, dump_model=203)

    assert status == 0
    assert (model_base, 100) in transport.reads
    assert (model_base + 100, 5) in transport.reads
    assert "     0      40004     40005        0  0x0000" in stdout
    assert "   104      40108     40109      104  0x0068" in stdout
    assert transport.close_called


def test_parser_accepts_device_id_and_dump_model() -> None:
    """The CLI accepts a non-default device ID and arbitrary model ID."""
    arguments = create_argument_parser().parse_args(
        ["--host", "192.168.2.11", "--device-id", "200", "--dump-model", "203"]
    )

    assert arguments.device_id == 200
    assert arguments.dump_model == 203


def test_parser_accepts_decode_mode() -> None:
    """Decoded inspection is an additive command-line switch."""
    arguments = create_argument_parser().parse_args(
        ["--host", "192.168.2.11", "--decode"]
    )
    assert arguments.decode


def test_decode_model_1_prints_real_identity_and_unavailable_marker() -> None:
    """Common Model identity strings use the production model decoder."""
    registers = model_chain((1, 65))
    payload = [0] * 65
    payload[0:16] = string_words("Fronius", 16)
    payload[16:32] = string_words("Symo GEN24 10.0", 16)
    payload[40:48] = string_words("1.41.10-1", 8)
    payload[48:64] = string_words("31520500", 16)
    payload[64] = 0xFFFF
    set_payload(registers, payload)
    transport = FakeTransport(registers)

    status, stdout, stderr = run_inspection(transport, decode=True)

    assert status == 0
    assert "Model 1 - Common" in stdout
    assert "Mn                   Fronius" in stdout
    assert "Md                   Symo GEN24 10.0" in stdout
    assert "DA                   unavailable" in stdout
    assert stderr == ""
    assert transport.close_called


def test_decode_fixed_model_prints_values_units_scale_factors_and_role() -> None:
    """Decoded fixed values include units, technical scale factors, and role."""
    registers = model_chain((103, 50))
    payload = decodable_payload(MODEL_103)
    payload[12] = 3354
    payload[13] = 0xFFFF
    payload[0] = 0xFFFF
    set_payload(registers, payload)

    status, stdout, _ = run_inspection(FakeTransport(registers), decode=True)

    assert status == 0
    assert "physical role: inverter" in stdout
    assert "A                    unavailable A" in stdout
    assert "W                    335.4 W" in stdout
    assert "W_SF                 -1" in stdout
    assert "  inverter" in stdout


def test_decode_model_160_prints_runtime_semantics_and_capability() -> None:
    """Repeating modules use core semantic classification without fixed counts."""
    registers = model_chain((160, 88))
    payload = decodable_payload(MODEL_160)
    payload[6] = 4
    for index, name in enumerate(("MPPT 1", "MPPT 2", "StCha 3", "StDisCha 4")):
        base = 8 + index * 20
        payload[base] = index + 1
        payload[base + 1 : base + 9] = string_words(name, 8)
    set_payload(registers, payload)

    status, stdout, _ = run_inspection(FakeTransport(registers), decode=True)

    assert status == 0
    assert "module instance 3" in stdout
    assert "IDStr                StDisCha 4" in stdout
    assert "semantic kind: mppt" in stdout
    assert "semantic kind: storage_charge" in stdout
    assert "semantic kind: storage_discharge" in stdout
    assert "physical role: storage" in stdout
    assert "  mppt" in stdout


def test_decode_model_203_uses_generic_chunked_reader() -> None:
    """The 105-register meter model is read through generic safe chunks."""
    registers = model_chain((203, 105))
    set_payload(registers, decodable_payload(MODEL_203))
    transport = FakeTransport(registers)

    status, stdout, _ = run_inspection(transport, decode=True)

    model_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    assert status == 0
    assert (model_base, 100) in transport.reads
    assert (model_base + 100, 5) in transport.reads
    assert "Model 203" in stdout
    assert "  meter" in stdout


def test_decode_reports_length_mismatch_and_continues_with_other_models() -> None:
    """A mismatched local definition is skipped without hiding later models."""
    transport = FakeTransport(model_chain((1, 4), (203, 105)))

    status, stdout, stderr = run_inspection(transport, decode=True)

    assert status == 0
    assert "Model 1: discovered length 4" in stderr
    assert "expected length 65" in stderr
    assert "Model 203" in stdout


def test_decode_unknown_model_reports_and_continues() -> None:
    """Unknown discovered models remain visible and do not abort decoding."""
    transport = FakeTransport(model_chain((999, 2), (1, 65)))

    status, stdout, stderr = run_inspection(transport, decode=True)

    assert status == 0
    assert "Model 999: no local definition available; skipped" in stdout
    assert "Model 1 - Common" in stdout
    assert stderr == ""
