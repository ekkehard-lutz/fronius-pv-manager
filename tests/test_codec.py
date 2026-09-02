"""Tests for generic Home Assistant-independent SunSpec register decoding."""

import pytest

from custom_components.fronius_pv_manager.codec import decode_register_value
from custom_components.fronius_pv_manager.models import (
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
)


def definition(
    data_type: RegisterDataType,
    size: int,
    **kwargs: object,
) -> RegisterDefinition:
    """Create a compact register definition for codec tests."""
    return RegisterDefinition(
        name="test_value",
        offset=0,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("data_type", "words", "expected"),
    [
        (RegisterDataType.UINT16, [0x1234], 0x1234),
        (RegisterDataType.INT16, [0x1234], 0x1234),
        (RegisterDataType.INT16, [0xFFFD], -3),
        (RegisterDataType.UINT32, [0x0001, 0x0002], 0x00010002),
        (RegisterDataType.INT32, [0xFFFF, 0xFFFD], -3),
        (
            RegisterDataType.UINT64,
            [0x0001, 0x0002, 0x0003, 0x0004],
            0x0001000200030004,
        ),
        (RegisterDataType.ACC32, [0x0001, 0x0002], 0x00010002),
        (
            RegisterDataType.ACC64,
            [0x0001, 0x0002, 0x0003, 0x0004],
            0x0001000200030004,
        ),
    ],
)
def test_numeric_decoding(
    data_type: RegisterDataType, words: list[int], expected: int
) -> None:
    """Numeric types use big-endian word order and signed conversion as needed."""
    result = decode_register_value(definition(data_type, len(words)), words)

    assert result.value == expected
    assert result.raw == int.from_bytes(
        b"".join(word.to_bytes(2, "big") for word in words), "big"
    )


@pytest.mark.parametrize(
    ("word", "expected"), [(0xFFFD, -3), (0xFFFE, -2), (0xFFFF, -1)]
)
def test_sunssf_negative_values_remain_valid(word: int, expected: int) -> None:
    """Valid negative SunSpec scale factors are not unsigned sentinels."""
    result = decode_register_value(
        definition(RegisterDataType.SUNSSF, 1), [word]
    )

    assert result.raw == word
    assert result.value == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MPPT 1", "MPPT 1"),
        ("StCha 3", "StCha 3"),
        ("StDisCha 4", "StDisCha 4"),
    ],
)
def test_real_model_160_strings(text: str, expected: str) -> None:
    """Observed model 160 ASCII strings decode without model-specific logic."""
    encoded = text.encode("ascii") + b"\x00"
    if len(encoded) % 2:
        encoded += b"\x00"
    words = [
        int.from_bytes(encoded[index : index + 2], "big")
        for index in range(0, len(encoded), 2)
    ]

    result = decode_register_value(
        definition(RegisterDataType.STRING, len(words)), words
    )

    assert result.value == expected


def test_string_strips_only_trailing_nuls_and_whitespace() -> None:
    """Internal spaces remain meaningful while trailing padding is removed."""
    words = [0x4120, 0x4209, 0x0000]

    result = decode_register_value(definition(RegisterDataType.STRING, 3), words)

    assert result.raw == "A B\t\x00\x00"
    assert result.value == "A B"


def test_invalid_ascii_uses_replacement_character() -> None:
    """Invalid ASCII bytes decode deterministically without raising."""
    result = decode_register_value(
        definition(RegisterDataType.STRING, 1), [0x41FF]
    )

    assert result.value == "A�"


def test_enum_known_and_unknown_values() -> None:
    """Known enum values map to labels while unknown valid values stay numeric."""
    item = definition(RegisterDataType.ENUM16, 1, enum={1: "active"})

    assert decode_register_value(item, [1]).value == "active"
    assert decode_register_value(item, [2]).value == 2


def test_bitfield16_returns_known_active_labels() -> None:
    """BITFIELD16 returns known labels while preserving the raw mask."""
    item = definition(
        RegisterDataType.BITFIELD16,
        1,
        bitfield={0x0001: "ready", 0x0004: "running"},
    )

    result = decode_register_value(item, [0x0005])

    assert result.raw == 0x0005
    assert result.value == "ready, running"


def test_bitfield32_uses_big_endian_word_order() -> None:
    """BITFIELD32 combines both words before selecting known flags."""
    item = definition(
        RegisterDataType.BITFIELD32,
        2,
        bitfield={0x00000002: "low", 0x00010000: "high"},
    )

    result = decode_register_value(item, [0x0001, 0x0002])

    assert result.raw == 0x00010002
    assert result.value == "low, high"


def test_unknown_bitfield_bits_do_not_invalidate_value() -> None:
    """Unknown set bits remain in raw without invalidating known labels."""
    item = definition(
        RegisterDataType.BITFIELD16, 1, bitfield={0x0001: "ready"}
    )

    result = decode_register_value(item, [0x0081])

    assert result.raw == 0x0081
    assert result.value == "ready"


@pytest.mark.parametrize(
    ("data_type", "size", "words"),
    [
        (RegisterDataType.UINT16, 1, [0xFFFF]),
        (RegisterDataType.INT16, 1, [0x8000]),
        (RegisterDataType.UINT32, 2, [0xFFFF, 0xFFFF]),
        (RegisterDataType.INT32, 2, [0x8000, 0x0000]),
        (RegisterDataType.UINT64, 4, [0xFFFF] * 4),
        (RegisterDataType.SUNSSF, 1, [0x8000]),
        (RegisterDataType.ENUM16, 1, [0xFFFF]),
        (RegisterDataType.BITFIELD16, 1, [0xFFFF]),
        (RegisterDataType.BITFIELD32, 2, [0xFFFF, 0xFFFF]),
        (RegisterDataType.ACC32, 2, [0x0000, 0x0000]),
        (RegisterDataType.ACC64, 4, [0x0000] * 4),
    ],
)
def test_default_invalid_sentinels(
    data_type: RegisterDataType, size: int, words: list[int]
) -> None:
    """Each numeric SunSpec type maps its not-implemented sentinel to None."""
    result = decode_register_value(definition(data_type, size), words)

    assert result.value is None
    assert result.raw is not None


def test_explicit_invalid_value_is_respected() -> None:
    """Definition-specific sentinels supplement default SunSpec rules."""
    item = definition(RegisterDataType.UINT16, 1, invalid_values=(123,))

    result = decode_register_value(item, [123])

    assert result == type(result)(raw=123, value=None)


def test_invalid_sentinel_is_detected_before_scaling() -> None:
    """Scaling never transforms an invalid raw value into a number."""
    result = decode_register_value(
        definition(RegisterDataType.UINT16, 1), [0xFFFF], scale_factor=-2
    )

    assert result.value is None


@pytest.mark.parametrize(
    ("word", "scale_factor", "expected"),
    [(12, 2, 1200), (123, -2, 1.23), (120, -1, 12), (12, 0, 12)],
)
def test_scale_factor_application(
    word: int, scale_factor: int, expected: int | float
) -> None:
    """Decimal scaling preserves integers where the result is exact."""
    result = decode_register_value(
        definition(RegisterDataType.UINT16, 1),
        [word],
        scale_factor=scale_factor,
    )

    assert result.value == expected
    assert type(result.value) is type(expected)


@pytest.mark.parametrize(
    "data_type", [RegisterDataType.ENUM16, RegisterDataType.SUNSSF]
)
def test_scale_factor_is_not_applied_to_non_scalable_types(
    data_type: RegisterDataType,
) -> None:
    """Metadata and scale-factor types are returned without secondary scaling."""
    result = decode_register_value(
        definition(data_type, 1), [2], scale_factor=3
    )

    assert result.value == 2


def test_wrong_register_count_is_rejected() -> None:
    """The caller must supply exactly the definition's declared word count."""
    with pytest.raises(ValueError, match="requires 2 words"):
        decode_register_value(definition(RegisterDataType.UINT32, 2), [1])


@pytest.mark.parametrize("word", [-1, 0x10000])
def test_register_word_outside_uint16_is_rejected(word: int) -> None:
    """Transport words must remain within their unsigned 16-bit range."""
    with pytest.raises(ValueError, match="0 through 65535"):
        decode_register_value(definition(RegisterDataType.UINT16, 1), [word])
