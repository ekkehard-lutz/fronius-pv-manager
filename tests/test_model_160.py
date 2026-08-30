"""Tests for the SunSpec Model 160 register-map definition."""

import pytest

from custom_components.fronius_pv_manager.codec import decode_register_value
from custom_components.fronius_pv_manager.models import RegisterDefinition
from custom_components.fronius_pv_manager.register_maps.model_160 import (
    MODEL_160,
    MODULE_BLOCK,
)

_FIXED_WORDS = [
    0xFFFD,
    0xFFFE,
    0xFFFF,
    0xFFFE,
    0xFFFF,
    0xFFFF,
    0x0004,
    0xFFFF,
]

_MODULE_IDENTITIES = (
    (8, 1, [0x4D50, 0x5054, 0x2031, 0, 0, 0, 0, 0], "MPPT 1"),
    (28, 2, [0x4D50, 0x5054, 0x2032, 0, 0, 0, 0, 0], "MPPT 2"),
    (48, 3, [0x5374, 0x4368, 0x6120, 0x3300, 0, 0, 0, 0], "StCha 3"),
    (
        68,
        4,
        [0x5374, 0x4469, 0x7343, 0x6861, 0x2034, 0, 0, 0],
        "StDisCha 4",
    ),
)


def register_by_name(
    registers: tuple[RegisterDefinition, ...], name: str
) -> RegisterDefinition:
    """Find a register by its unique name in one definition section."""
    return next(register for register in registers if register.name == name)


def decode_from_payload(
    register: RegisterDefinition,
    payload: list[int],
    *,
    base_offset: int = 0,
    scale_factor: int | None = None,
):
    """Decode a definition from its model-relative location in a test payload."""
    start = base_offset + register.offset
    return decode_register_value(
        register,
        payload[start : start + register.size],
        scale_factor=scale_factor,
    )


def test_model_160_structure() -> None:
    """Model identity, payload length, and fixed offsets match the register map."""
    assert MODEL_160.model_ids == (160,)
    assert MODEL_160.expected_length == 88
    assert {register.name: register.offset for register in MODEL_160.registers} == {
        "DCA_SF": 0,
        "DCV_SF": 1,
        "DCW_SF": 2,
        "DCWH_SF": 3,
        "Evt": 4,
        "N": 6,
        "TmsPer": 7,
    }
    assert MODEL_160.repeating_blocks == (MODULE_BLOCK,)
    assert MODULE_BLOCK.name == "module"
    assert MODULE_BLOCK.offset == 8
    assert MODULE_BLOCK.block_size == 20


def test_module_register_layout() -> None:
    """Every module register has the expected block-relative range."""
    expected = {
        "ID": (0, 1),
        "IDStr": (1, 8),
        "DCA": (9, 1),
        "DCV": (10, 1),
        "DCW": (11, 1),
        "DCWH": (12, 2),
        "Tms": (14, 2),
        "Tmp": (16, 1),
        "DCSt": (17, 1),
        "DCEvt": (18, 2),
    }

    assert {
        register.name: (register.offset, register.size)
        for register in MODULE_BLOCK.registers
    } == expected
    assert all(
        register.offset + register.size <= MODULE_BLOCK.block_size
        for register in MODULE_BLOCK.registers
    )


def test_concrete_module_bases_fit_discovered_length() -> None:
    """Four observed bases fit, while a theoretical fifth base does not."""
    discovered_length = 88
    assert all(
        base + MODULE_BLOCK.block_size <= discovered_length
        for base in (8, 28, 48, 68)
    )
    assert 88 + MODULE_BLOCK.block_size > discovered_length


def test_real_fixed_values_decode() -> None:
    """Observed GEN24 fixed words decode with generic SunSpec rules."""
    decoded = {
        register.name: decode_from_payload(register, _FIXED_WORDS).value
        for register in MODEL_160.registers
        if register.name in {"DCA_SF", "DCV_SF", "DCW_SF", "DCWH_SF", "N"}
    }

    assert decoded == {
        "DCA_SF": -3,
        "DCV_SF": -2,
        "DCW_SF": -1,
        "DCWH_SF": -2,
        "N": 4,
    }


@pytest.mark.parametrize(
    ("base_offset", "expected_id", "id_words", "expected_string"),
    _MODULE_IDENTITIES,
)
def test_real_module_identities_decode(
    base_offset: int,
    expected_id: int,
    id_words: list[int],
    expected_string: str,
) -> None:
    """Observed module identifiers decode without semantic classification."""
    payload = [0] * 88
    payload[base_offset] = expected_id
    payload[base_offset + 1 : base_offset + 9] = id_words
    id_register = register_by_name(MODULE_BLOCK.registers, "ID")
    string_register = register_by_name(MODULE_BLOCK.registers, "IDStr")

    assert (
        decode_from_payload(id_register, payload, base_offset=base_offset).value
        == expected_id
    )
    assert (
        decode_from_payload(string_register, payload, base_offset=base_offset).value
        == expected_string
    )


@pytest.mark.parametrize(
    ("name", "words", "expected"),
    [
        ("DCA", [1234], 1.234),
        ("DCV", [5123], 51.23),
        ("DCW", [5000], 500),
        ("DCWH", [0, 12345], 123.45),
    ],
)
def test_module_measurements_resolve_named_fixed_scale_factors(
    name: str, words: list[int], expected: int | float
) -> None:
    """Each measurement definition names the correct decoded fixed scale factor."""
    fixed_values = {
        register.name: decode_from_payload(register, _FIXED_WORDS).value
        for register in MODEL_160.registers
    }
    register = register_by_name(MODULE_BLOCK.registers, name)
    assert register.scale_factor is not None
    scale_factor = fixed_values[register.scale_factor]
    assert isinstance(scale_factor, int)

    result = decode_register_value(register, words, scale_factor=scale_factor)

    assert result.value == expected
