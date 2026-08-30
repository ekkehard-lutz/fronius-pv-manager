"""Tests for the Fronius GEN24 SunSpec Model 123 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
    ValueRange,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_123

_GEN24_PAYLOAD = [
    0x0000,
    0x0000,
    0x0001,
    0x2710,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x03E8,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x8000,
    0x0064,
    0x8000,
    0x0000,
    0x0000,
    0x0000,
    0x0002,
    0x0000,
    0xFFFE,
    0xFFFD,
    0x0000,
]

_EXPECTED_LAYOUT = (
    ("Conn_WinTms", 0, RegisterDataType.UINT16),
    ("Conn_RvrtTms", 1, RegisterDataType.UINT16),
    ("Conn", 2, RegisterDataType.BITFIELD16),
    ("WMaxLimPct", 3, RegisterDataType.UINT16),
    ("WMaxLimPct_WinTms", 4, RegisterDataType.UINT16),
    ("WMaxLimPct_RvrtTms", 5, RegisterDataType.UINT16),
    ("WMaxLimPct_RmpTms", 6, RegisterDataType.UINT16),
    ("WMaxLim_Ena", 7, RegisterDataType.ENUM16),
    ("OutPFSet", 8, RegisterDataType.INT16),
    ("OutPFSet_WinTms", 9, RegisterDataType.UINT16),
    ("OutPFSet_RvrtTms", 10, RegisterDataType.UINT16),
    ("OutPFSet_RmpTms", 11, RegisterDataType.UINT16),
    ("OutPFSet_Ena", 12, RegisterDataType.ENUM16),
    ("VArWMaxPct", 13, RegisterDataType.INT16),
    ("VArMaxPct", 14, RegisterDataType.INT16),
    ("VArAvalPct", 15, RegisterDataType.INT16),
    ("VArPct_WinTms", 16, RegisterDataType.UINT16),
    ("VArPct_RvrtTms", 17, RegisterDataType.UINT16),
    ("VArPct_RmpTms", 18, RegisterDataType.UINT16),
    ("VArPct_Mod", 19, RegisterDataType.ENUM16),
    ("VArPct_Ena", 20, RegisterDataType.ENUM16),
    ("WMaxLimPct_SF", 21, RegisterDataType.SUNSSF),
    ("OutPFSet_SF", 22, RegisterDataType.SUNSSF),
    ("VArPct_SF", 23, RegisterDataType.SUNSSF),
)

_READ_ONLY_NAMES = {
    "WMaxLimPct_RmpTms",
    "VArWMaxPct",
    "VArAvalPct",
    "VArPct_Mod",
    "WMaxLimPct_SF",
    "OutPFSet_SF",
    "VArPct_SF",
}


def test_model_123_structure_and_access_match_worksheet() -> None:
    """The complete controls layout preserves its worksheet access metadata."""
    assert MODEL_123.model_ids == (123,)
    assert MODEL_123.expected_length == 24
    assert MODEL_123.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.data_type)
        for register in MODEL_123.registers
    ) == _EXPECTED_LAYOUT
    assert all(register.size == 1 for register in MODEL_123.registers)

    read_only = {
        register.name
        for register in MODEL_123.registers
        if register.access is RegisterAccess.READ_ONLY
    }
    read_write = {
        register.name
        for register in MODEL_123.registers
        if register.access is RegisterAccess.READ_WRITE
    }
    assert read_only == _READ_ONLY_NAMES
    assert len(read_only) == 7
    assert len(read_write) == 17


def test_model_123_layout_has_no_gaps_or_overlaps() -> None:
    """The worksheet fields cover every payload word exactly once."""
    occupied_offsets = [
        offset
        for register in MODEL_123.registers
        for offset in range(register.offset, register.offset + register.size)
    ]

    assert occupied_offsets == list(range(24))


def test_model_123_valid_ranges_match_worksheet() -> None:
    """Only worksheet timing bounds are recorded as valid ranges."""
    registers = {register.name: register for register in MODEL_123.registers}
    ranges = {
        name: register.valid_range
        for name, register in registers.items()
        if register.valid_range is not None
    }

    assert ranges == {
        "WMaxLimPct_WinTms": ValueRange(minimum=0, maximum=300),
        "WMaxLimPct_RvrtTms": ValueRange(minimum=0, maximum=28800),
        "OutPFSet_WinTms": ValueRange(minimum=0, maximum=300),
        "OutPFSet_RvrtTms": ValueRange(minimum=0, maximum=28800),
        "VArPct_WinTms": ValueRange(minimum=0, maximum=300),
        "VArPct_RvrtTms": ValueRange(minimum=0, maximum=28800),
    }


def test_model_123_scale_links_resolve() -> None:
    """Every exact scale-factor reference names a local register."""
    names = {register.name for register in MODEL_123.registers}
    links = {
        register.name: register.scale_factor
        for register in MODEL_123.registers
        if register.scale_factor is not None
    }

    assert links == {
        "WMaxLimPct": "WMaxLimPct_SF",
        "OutPFSet": "OutPFSet_SF",
        "VArWMaxPct": "VArPct_SF",
        "VArMaxPct": "VArPct_SF",
        "VArAvalPct": "VArPct_SF",
    }
    assert set(links.values()) <= names
    assert all(
        register.poll_class is PollClass.STATIC
        for register in MODEL_123.registers
        if register.data_type is RegisterDataType.SUNSSF
    )


def test_real_gen24_immediate_controls_decode() -> None:
    """Observed control values decode through the generic model decoder."""
    values = decode_model(MODEL_123, _GEN24_PAYLOAD).fixed

    assert values["Conn_WinTms"].value == 0
    assert values["Conn_RvrtTms"].value == 0
    assert values["Conn"].raw == 1
    assert values["Conn"].value == "Connected"
    assert values["WMaxLimPct"].value == 100
    assert values["WMaxLim_Ena"].value == "Disabled"
    assert values["OutPFSet"].value == 1
    assert values["OutPFSet_Ena"].value == "Disabled"
    assert values["VArMaxPct"].value == 100
    assert values["VArPct_Mod"].value == "VAR limit as a % of VArMax"
    assert values["VArPct_Ena"].value == "Disabled"


def test_real_gen24_invalid_and_valid_scale_factors() -> None:
    """Observed sentinels remain distinct from valid negative scale factors."""
    values = decode_model(MODEL_123, _GEN24_PAYLOAD).fixed

    assert values["VArWMaxPct"].raw == 0x8000
    assert values["VArWMaxPct"].value is None
    assert values["VArAvalPct"].raw == 0x8000
    assert values["VArAvalPct"].value is None
    assert values["WMaxLimPct_SF"].raw == 0xFFFE
    assert values["WMaxLimPct_SF"].value == -2
    assert values["OutPFSet_SF"].raw == 0xFFFD
    assert values["OutPFSet_SF"].value == -3
    assert values["VArPct_SF"].value == 0


def test_model_123_enum_and_bitfield_metadata() -> None:
    """Worksheet connection and control meanings remain available."""
    registers = {register.name: register for register in MODEL_123.registers}

    assert registers["Conn"].bitfield == {0x0001: "Connected"}
    assert registers["WMaxLim_Ena"].enum == {0: "Disabled", 1: "Enabled"}
    assert registers["OutPFSet_Ena"].enum == {0: "Disabled", 1: "Enabled"}
    assert registers["VArPct_Mod"].enum == {
        2: "VAR limit as a % of VArMax"
    }
    assert registers["VArPct_Ena"].enum == {0: "Disabled", 1: "Enabled"}
