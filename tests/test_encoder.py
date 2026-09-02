"""Tests for safe Home Assistant-independent register value encoding."""

from decimal import Decimal

import pytest

from custom_components.fronius_pv_manager.codec import (
    decode_register_value,
    encode_register_value,
)
from custom_components.fronius_pv_manager.models import (
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    ValueRange,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_123, MODEL_124


def writable(
    data_type: RegisterDataType,
    *,
    size: int = 1,
    valid_range: ValueRange | None = None,
    enum: dict[int, str] | None = None,
    bitfield: dict[int, str] | None = None,
    invalid_values: tuple[int, ...] = (),
) -> RegisterDefinition:
    """Create a compact writable definition for encoder tests."""
    return RegisterDefinition(
        name="value",
        offset=0,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_WRITE,
        valid_range=valid_range,
        enum=enum,
        bitfield=bitfield,
        invalid_values=invalid_values,
    )


def model_register(model, name: str) -> RegisterDefinition:
    """Find one actual writable register definition by name."""
    return next(register for register in model.registers if register.name == name)


@pytest.mark.parametrize(
    ("definition", "value", "expected"),
    [
        (writable(RegisterDataType.UINT16), 1234, (0x04D2,)),
        (writable(RegisterDataType.INT16), 1234, (0x04D2,)),
        (writable(RegisterDataType.INT16), -1, (0xFFFF,)),
        (writable(RegisterDataType.UINT32, size=2), 0x12345678, (0x1234, 0x5678)),
        (writable(RegisterDataType.INT32, size=2), 123456, (0x0001, 0xE240)),
        (writable(RegisterDataType.INT32, size=2), -1, (0xFFFF, 0xFFFF)),
        (
            writable(RegisterDataType.UINT64, size=4),
            0x123456789ABCDEF0,
            (0x1234, 0x5678, 0x9ABC, 0xDEF0),
        ),
        (writable(RegisterDataType.SUNSSF), -2, (0xFFFE,)),
    ],
)
def test_numeric_types_and_word_ordering(definition, value, expected) -> None:
    """Supported integer types emit the decoder's big-endian register order."""
    assert encode_register_value(definition, value) == expected


def test_enum_accepts_only_canonical_numeric_choices() -> None:
    """Enum writes must select a value declared by the register definition."""
    definition = writable(
        RegisterDataType.ENUM16, enum={0: "Disabled", 1: "Enabled"}
    )
    assert encode_register_value(definition, 1) == (1,)
    with pytest.raises(ValueError, match="enum"):
        encode_register_value(definition, 2)
    with pytest.raises(ValueError, match="integer"):
        encode_register_value(definition, 1.0)


def test_bitfield16_and_bitfield32_accept_known_raw_masks() -> None:
    """Raw bit masks compose only bits described by existing metadata."""
    bitfield = {0x0001: "ready", 0x0002: "warning"}
    assert encode_register_value(
        writable(RegisterDataType.BITFIELD16, bitfield=bitfield), 3
    ) == (3,)
    assert encode_register_value(
        writable(RegisterDataType.BITFIELD32, size=2, bitfield=bitfield), 2
    ) == (0, 2)
    with pytest.raises(ValueError, match="unknown bits"):
        encode_register_value(
            writable(RegisterDataType.BITFIELD16, bitfield=bitfield), 4
        )
    with pytest.raises(ValueError, match="integer"):
        encode_register_value(
            writable(RegisterDataType.BITFIELD16, bitfield=bitfield), 1.0
        )


@pytest.mark.parametrize(
    ("value", "scale_factor", "expected"),
    [
        (12, 0, (12,)),
        (12.34, -2, (1234,)),
        (Decimal("12.34"), -2, (1234,)),
        (120, 1, (12,)),
    ],
)
def test_scaling_is_inverted_exactly(value, scale_factor, expected) -> None:
    """Positive, zero, and negative scale factors produce integral raw values."""
    definition = writable(RegisterDataType.UINT16)
    assert encode_register_value(definition, value, scale_factor) == expected


def test_non_integral_raw_result_is_rejected() -> None:
    """Inverse scaling never truncates a fractional raw register value."""
    with pytest.raises(ValueError, match="integral"):
        encode_register_value(writable(RegisterDataType.UINT16), 12.345, -2)


def test_valid_range_boundaries_and_step() -> None:
    """Semantic range boundaries and increments are enforced before scaling."""
    definition = writable(
        RegisterDataType.UINT16,
        valid_range=ValueRange(minimum=10, maximum=20, step=2),
    )
    assert encode_register_value(definition, 10) == (10,)
    assert encode_register_value(definition, 20) == (20,)
    with pytest.raises(ValueError, match="below"):
        encode_register_value(definition, 9)
    with pytest.raises(ValueError, match="above"):
        encode_register_value(definition, 21)
    with pytest.raises(ValueError, match="step"):
        encode_register_value(definition, 11)


def test_read_only_and_unsupported_types_are_rejected() -> None:
    """Encoding cannot bypass access metadata or unsupported codec scope."""
    read_only = RegisterDefinition(
        name="readonly",
        offset=0,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=RegisterAccess.READ_ONLY,
    )
    with pytest.raises(ValueError, match="read-only"):
        encode_register_value(read_only, 1)
    with pytest.raises(ValueError, match="not supported"):
        encode_register_value(writable(RegisterDataType.STRING), "text")


@pytest.mark.parametrize(
    ("definition", "value", "message"),
    [
        (writable(RegisterDataType.UINT16), -1, "range"),
        (writable(RegisterDataType.UINT16), 0x10000, "range"),
        (writable(RegisterDataType.INT16), 0x8000, "range"),
        (writable(RegisterDataType.UINT32, size=2), 0x100000000, "range"),
        (writable(RegisterDataType.BITFIELD16), -1, "negative"),
    ],
)
def test_raw_type_overflow_and_malformed_values_are_rejected(
    definition, value, message
) -> None:
    """Raw target representation is checked without clamping."""
    with pytest.raises(ValueError, match=message):
        encode_register_value(definition, value)


@pytest.mark.parametrize(
    ("definition", "value"),
    [
        (writable(RegisterDataType.UINT16), 0xFFFF),
        (writable(RegisterDataType.INT16), -0x8000),
        (writable(RegisterDataType.UINT32, size=2), 0xFFFFFFFF),
        (writable(RegisterDataType.INT32, size=2), -0x80000000),
        (writable(RegisterDataType.BITFIELD16), 0xFFFF),
        (writable(RegisterDataType.UINT16, invalid_values=(123,)), 123),
    ],
)
def test_invalid_sentinel_collisions_are_rejected(definition, value) -> None:
    """Callers cannot intentionally write unavailable SunSpec patterns."""
    with pytest.raises(ValueError, match="sentinel"):
        encode_register_value(definition, value)


@pytest.mark.parametrize("value", [None, True, "12", float("nan"), float("inf")])
def test_non_numeric_or_non_finite_values_are_rejected(value) -> None:
    """Only finite numeric semantic values enter the encoding pipeline."""
    with pytest.raises(ValueError, match="finite numeric"):
        encode_register_value(writable(RegisterDataType.UINT16), value)


@pytest.mark.parametrize(
    ("name", "value", "scale_factor"),
    [
        ("Conn", 1, None),
        ("WMaxLimPct", 50, -2),
        ("WMaxLimPct_WinTms", 300, None),
        ("WMaxLim_Ena", 1, None),
        ("OutPFSet", 0.95, -3),
        ("VArMaxPct", -20, -2),
    ],
)
def test_representative_model_123_writable_registers(
    name: str, value: object, scale_factor: int | None
) -> None:
    """Immediate-control definitions encode representative safe values."""
    assert encode_register_value(
        model_register(MODEL_123, name), value, scale_factor
    )


@pytest.mark.parametrize(
    ("name", "value", "scale_factor"),
    [
        ("VAChaMax", 1000, 0),
        ("MinRsvPct", 10, -2),
        ("OutWRte", -50, -2),
        ("InWRte", -50, -2),
        ("InOutWRte_RvrtTms", 300, None),
        ("ChaGriSet", 1, None),
    ],
)
def test_representative_model_124_writable_registers(
    name: str, value: object, scale_factor: int | None
) -> None:
    """Storage-control definitions encode representative safe values."""
    assert encode_register_value(
        model_register(MODEL_124, name), value, scale_factor
    )


@pytest.mark.parametrize(
    ("definition", "value", "scale_factor"),
    [
        (writable(RegisterDataType.UINT16), 12.34, -2),
        (writable(RegisterDataType.INT16), -12.34, -2),
        (writable(RegisterDataType.UINT32, size=2), 123456, 0),
        (writable(RegisterDataType.INT32, size=2), -123456, 0),
    ],
)
def test_encode_decode_round_trip(definition, value, scale_factor) -> None:
    """Representative encoder output decodes to the requested semantic value."""
    words = encode_register_value(definition, value, scale_factor)
    decoded = decode_register_value(definition, words, scale_factor)
    assert decoded.value == value
