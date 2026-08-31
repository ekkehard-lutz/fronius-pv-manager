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
from decimal import Decimal, InvalidOperation

from .models import (
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
)

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

_ENCODABLE_TYPES = {
    RegisterDataType.UINT16,
    RegisterDataType.INT16,
    RegisterDataType.UINT32,
    RegisterDataType.INT32,
    RegisterDataType.UINT64,
    RegisterDataType.SUNSSF,
    RegisterDataType.ENUM16,
    RegisterDataType.BITFIELD16,
    RegisterDataType.BITFIELD32,
}

_UNSIGNED_TYPES = {
    RegisterDataType.UINT16,
    RegisterDataType.UINT32,
    RegisterDataType.UINT64,
    RegisterDataType.ENUM16,
    RegisterDataType.BITFIELD16,
    RegisterDataType.BITFIELD32,
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


def encode_register_value(
    definition: RegisterDefinition,
    value: object,
    scale_factor: int | None = None,
) -> tuple[int, ...]:
    """Encode one semantic value as validated big-endian 16-bit words."""
    if definition.access is not RegisterAccess.READ_WRITE:
        raise ValueError(f"register {definition.name!r} is read-only")
    if definition.data_type not in _ENCODABLE_TYPES:
        raise ValueError(f"encoding {definition.data_type.value} is not supported")
    expected_size = _EXPECTED_SIZES[definition.data_type]
    if definition.size != expected_size:
        raise ValueError(
            f"{definition.data_type.value} requires {expected_size} register words"
        )
    if scale_factor is not None and type(scale_factor) is not int:
        raise ValueError("scale_factor must be an integer or None")
    if scale_factor is not None and definition.data_type not in _SCALABLE_TYPES:
        raise ValueError(f"{definition.data_type.value} does not support scaling")
    if definition.data_type in {
        RegisterDataType.ENUM16,
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    } and type(value) is not int:
        raise ValueError(f"{definition.data_type.value} value must be an integer")

    requested = _decimal_value(value)
    _validate_requested_range(definition, requested)
    raw_decimal = _remove_scale(requested, scale_factor)
    if raw_decimal != raw_decimal.to_integral_value():
        raise ValueError("value cannot be represented as an integral raw register")
    raw_value = int(raw_decimal)

    if definition.data_type is RegisterDataType.ENUM16:
        if definition.enum is not None and raw_value not in definition.enum:
            raise ValueError(f"{raw_value} is not a valid enum value")
    if definition.data_type in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        _validate_bitfield(definition, raw_value)

    bits = definition.size * 16
    if definition.data_type in _UNSIGNED_TYPES:
        minimum, maximum = 0, (1 << bits) - 1
    else:
        minimum, maximum = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not minimum <= raw_value <= maximum:
        raise ValueError(
            f"raw value {raw_value} is outside the {definition.data_type.value} range"
        )

    combined = raw_value if raw_value >= 0 else raw_value + (1 << bits)
    if combined in definition.invalid_values or combined == _DEFAULT_INVALID_VALUES.get(
        definition.data_type
    ):
        raise ValueError("encoded value collides with an invalid sentinel")
    return tuple(
        (combined >> shift) & 0xFFFF
        for shift in range((definition.size - 1) * 16, -1, -16)
    )


def _decimal_value(value: object) -> Decimal:
    """Convert a caller value without inheriting binary float representation noise."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("value must be a finite numeric value")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as err:
        raise ValueError("value must be a finite numeric value") from err
    if not converted.is_finite():
        raise ValueError("value must be a finite numeric value")
    return converted


def _remove_scale(value: Decimal, scale_factor: int | None) -> Decimal:
    """Invert SunSpec decimal scaling without rounding or truncation."""
    if scale_factor is None:
        return value
    return value / (Decimal(10) ** scale_factor)


def _validate_requested_range(
    definition: RegisterDefinition, requested: Decimal
) -> None:
    """Validate semantic bounds and optional increments before scaling."""
    value_range = definition.valid_range
    if value_range is None:
        return
    minimum = (
        Decimal(str(value_range.minimum))
        if value_range.minimum is not None
        else None
    )
    maximum = (
        Decimal(str(value_range.maximum))
        if value_range.maximum is not None
        else None
    )
    if minimum is not None and requested < minimum:
        raise ValueError("value is below the valid range")
    if maximum is not None and requested > maximum:
        raise ValueError("value is above the valid range")
    if value_range.step is not None:
        step = Decimal(str(value_range.step))
        origin = minimum if minimum is not None else Decimal(0)
        if (requested - origin) % step:
            raise ValueError("value does not match the valid range step")


def _validate_bitfield(definition: RegisterDefinition, raw_value: int) -> None:
    """Reject negative or unknown bit masks before representation checks."""
    if raw_value < 0:
        raise ValueError("bitfield value must not be negative")
    if definition.bitfield is not None:
        known_mask = 0
        for mask in definition.bitfield:
            known_mask |= mask
        if raw_value & ~known_mask:
            raise ValueError("bitfield value contains unknown bits")


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
