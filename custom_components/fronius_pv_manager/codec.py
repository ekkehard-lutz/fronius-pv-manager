"""Decode raw 16-bit SunSpec register words without transport dependencies.

Strings use ASCII decoding with replacement characters for invalid bytes. This
makes malformed device data deterministic while retaining its position in the
raw string. Only trailing NUL characters and whitespace are removed from the
decoded value.

Bitfields decode to a comma-separated string of known active labels ordered by
ascending mask. Unknown set bits are ignored, and the complete numeric bitmask
always remains available in :class:`RegisterValue.raw`.
"""

from collections.abc import Sequence

from .models import RegisterDataType, RegisterDefinition, RegisterValue

_EXPECTED_SIZES = {
    RegisterDataType.UINT16: 1,
    RegisterDataType.INT16: 1,
    RegisterDataType.UINT32: 2,
    RegisterDataType.INT32: 2,
    RegisterDataType.UINT64: 4,
    RegisterDataType.SUNSSF: 1,
    RegisterDataType.ENUM16: 1,
    RegisterDataType.BITFIELD16: 1,
    RegisterDataType.BITFIELD32: 2,
    RegisterDataType.ACC32: 2,
    RegisterDataType.ACC64: 4,
}

# These sentinels are expressed as their combined unsigned transport values.
# SUNSSF intentionally uses 0x8000, leaving -3, -2, and -1 valid.
_DEFAULT_INVALID_VALUES = {
    RegisterDataType.UINT16: 0xFFFF,
    RegisterDataType.INT16: 0x8000,
    RegisterDataType.UINT32: 0xFFFFFFFF,
    RegisterDataType.INT32: 0x80000000,
    RegisterDataType.UINT64: 0xFFFFFFFFFFFFFFFF,
    RegisterDataType.SUNSSF: 0x8000,
    RegisterDataType.ENUM16: 0xFFFF,
    RegisterDataType.BITFIELD16: 0xFFFF,
    RegisterDataType.BITFIELD32: 0xFFFFFFFF,
    RegisterDataType.ACC32: 0x00000000,
    RegisterDataType.ACC64: 0x0000000000000000,
}

_SIGNED_TYPES = {
    RegisterDataType.INT16,
    RegisterDataType.INT32,
    RegisterDataType.SUNSSF,
}

_SCALABLE_TYPES = {
    RegisterDataType.UINT16,
    RegisterDataType.INT16,
    RegisterDataType.UINT32,
    RegisterDataType.INT32,
    RegisterDataType.UINT64,
    RegisterDataType.ACC32,
    RegisterDataType.ACC64,
}


def decode_register_value(
    definition: RegisterDefinition,
    registers: Sequence[int],
    scale_factor: int | None = None,
) -> RegisterValue:
    """Decode one register definition from exactly its raw 16-bit words."""
    words = tuple(registers)
    if len(words) != definition.size:
        raise ValueError(
            f"register {definition.name!r} requires {definition.size} words, "
            f"received {len(words)}"
        )
    if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
        raise ValueError("raw register words must be integers from 0 through 65535")
    if scale_factor is not None and type(scale_factor) is not int:
        raise ValueError("scale_factor must be an integer or None")

    expected_size = _EXPECTED_SIZES.get(definition.data_type)
    if expected_size is not None and definition.size != expected_size:
        raise ValueError(
            f"{definition.data_type.value} requires {expected_size} register words"
        )

    if definition.data_type is RegisterDataType.STRING:
        return _decode_string(words)

    raw = _combine_words(words)
    if raw in definition.invalid_values or raw == _DEFAULT_INVALID_VALUES.get(
        definition.data_type
    ):
        return RegisterValue(raw=raw, value=None)

    if definition.data_type in _SIGNED_TYPES:
        value: int | float | str = _decode_signed(raw, definition.size * 16)
    elif definition.data_type is RegisterDataType.ENUM16:
        value = definition.enum.get(raw, raw) if definition.enum is not None else raw
    elif definition.data_type in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        value = _decode_bitfield(raw, definition)
    else:
        value = raw

    if scale_factor is not None and definition.data_type in _SCALABLE_TYPES:
        value = _apply_scale(value, scale_factor)
    return RegisterValue(raw=raw, value=value)


def _combine_words(words: tuple[int, ...]) -> int:
    """Combine big-endian 16-bit words into one unsigned integer."""
    value = 0
    for word in words:
        value = (value << 16) | word
    return value


def _decode_signed(raw: int, bits: int) -> int:
    """Decode an unsigned transport value using two's complement."""
    sign_bit = 1 << (bits - 1)
    return raw - (1 << bits) if raw & sign_bit else raw


def _decode_string(words: tuple[int, ...]) -> RegisterValue:
    """Decode high-byte-first ASCII with deterministic replacement behavior."""
    encoded = b"".join(word.to_bytes(2, byteorder="big") for word in words)
    raw = encoded.decode("ascii", errors="replace")
    return RegisterValue(raw=raw, value=raw.rstrip("\x00 \t\r\n\v\f"))


def _decode_bitfield(raw: int, definition: RegisterDefinition) -> str:
    """Return known active bit-mask labels in deterministic mask order."""
    if definition.bitfield is None:
        return ""
    labels = [
        label
        for mask, label in sorted(definition.bitfield.items())
        if mask != 0 and raw & mask == mask
    ]
    return ", ".join(labels)


def _apply_scale(value: int | float | str, scale_factor: int) -> int | float:
    """Apply a decimal scale while retaining exact integral results."""
    if not isinstance(value, int):
        raise ValueError("only numeric register values can be scaled")
    if scale_factor >= 0:
        return value * 10**scale_factor
    divisor = 10 ** (-scale_factor)
    if value % divisor == 0:
        return value // divisor
    return value / divisor
