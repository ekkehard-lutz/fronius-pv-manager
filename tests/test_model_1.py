"""Tests for the standard SunSpec Common Model 1 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterDataType,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_1

_GEN24_PAYLOAD = [
    0x4672,
    0x6F6E,
    0x6975,
    0x7300,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x5379,
    0x6D6F,
    0x2047,
    0x454E,
    0x3234,
    0x2031,
    0x302E,
    0x3000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x312E,
    0x3431,
    0x2E31,
    0x302D,
    0x3100,
    0x0000,
    0x0000,
    0x0000,
    0x3331,
    0x3532,
    0x3035,
    0x3030,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0001,
]


def test_model_1_structure() -> None:
    """The Common Model fields occupy the exact standard 65-word layout."""
    assert MODEL_1.model_ids == (1,)
    assert MODEL_1.expected_length == 65
    assert MODEL_1.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.size, register.data_type)
        for register in MODEL_1.registers
    ) == (
        ("Mn", 0, 16, RegisterDataType.STRING),
        ("Md", 16, 16, RegisterDataType.STRING),
        ("Opt", 32, 8, RegisterDataType.STRING),
        ("Vr", 40, 8, RegisterDataType.STRING),
        ("SN", 48, 16, RegisterDataType.STRING),
        ("DA", 64, 1, RegisterDataType.UINT16),
    )
    assert all(
        register.poll_class is PollClass.STATIC for register in MODEL_1.registers
    )
    assert all(
        current.offset + current.size <= following.offset
        for current, following in zip(
            MODEL_1.registers, MODEL_1.registers[1:], strict=False
        )
    )
    assert all(
        register.offset + register.size <= 65 for register in MODEL_1.registers
    )
    assert MODEL_1.registers[-1].offset == 64
    assert MODEL_1.registers[-1].offset + MODEL_1.registers[-1].size == 65


def test_real_gen24_common_identity_decodes() -> None:
    """The observed GEN24 Common Model payload decodes through generic logic."""
    values = decode_model(MODEL_1, _GEN24_PAYLOAD).fixed

    assert values["Mn"].value == "Fronius"
    assert values["Md"].value == "Symo GEN24 10.0"
    assert values["Opt"].value == ""
    assert values["Vr"].value == "1.41.10-1"
    assert values["SN"].value == "31520500"
    assert values["DA"].value == 1


def test_real_gen24_raw_identity_strings_remain_available() -> None:
    """Decoded identity values retain their untrimmed transport-level strings."""
    values = decode_model(MODEL_1, _GEN24_PAYLOAD).fixed

    assert values["Mn"].raw is not None
    assert values["Mn"].raw.startswith("Fronius\x00")
    assert len(values["Mn"].raw) == 32
    assert values["Md"].raw is not None
    assert values["Md"].raw.startswith("Symo GEN24 10.0\x00")
    assert len(values["Md"].raw) == 32
    assert values["Opt"].raw == "\x00" * 16
    assert values["Vr"].raw is not None
    assert values["Vr"].raw.startswith("1.41.10-1\x00")
    assert values["SN"].raw is not None
    assert values["SN"].raw.startswith("31520500\x00")
