"""Tests for the Fronius GEN24 SunSpec Model 124 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
    ValueRange,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_124

_GEN24_PAYLOAD = [
    0x2800,
    0x0064,
    0x0064,
    0x0000,
    0xFFFF,
    0x02BC,
    0x1F5E,
    0xFFFF,
    0xFFFF,
    0x0003,
    0x2710,
    0x2710,
    0xFFFF,
    0x0000,
    0xFFFF,
    0x0001,
    0x0000,
    0x0000,
    0x8000,
    0xFFFE,
    0xFFFE,
    0x8000,
    0x8000,
    0xFFFE,
]

_EXPECTED_LAYOUT = (
    ("WChaMax", 0, RegisterDataType.UINT16),
    ("WChaGra", 1, RegisterDataType.UINT16),
    ("WDisChaGra", 2, RegisterDataType.UINT16),
    ("StorCtl_Mod", 3, RegisterDataType.BITFIELD16),
    ("VAChaMax", 4, RegisterDataType.UINT16),
    ("MinRsvPct", 5, RegisterDataType.UINT16),
    ("ChaState", 6, RegisterDataType.UINT16),
    ("StorAval", 7, RegisterDataType.UINT16),
    ("InBatV", 8, RegisterDataType.UINT16),
    ("ChaSt", 9, RegisterDataType.ENUM16),
    ("OutWRte", 10, RegisterDataType.INT16),
    ("InWRte", 11, RegisterDataType.INT16),
    ("InOutWRte_WinTms", 12, RegisterDataType.UINT16),
    ("InOutWRte_RvrtTms", 13, RegisterDataType.UINT16),
    ("InOutWRte_RmpTms", 14, RegisterDataType.UINT16),
    ("ChaGriSet", 15, RegisterDataType.ENUM16),
    ("WChaMax_SF", 16, RegisterDataType.SUNSSF),
    ("WChaDisChaGra_SF", 17, RegisterDataType.SUNSSF),
    ("VAChaMax_SF", 18, RegisterDataType.SUNSSF),
    ("MinRsvPct_SF", 19, RegisterDataType.SUNSSF),
    ("ChaState_SF", 20, RegisterDataType.SUNSSF),
    ("StorAval_SF", 21, RegisterDataType.SUNSSF),
    ("InBatV_SF", 22, RegisterDataType.SUNSSF),
    ("InOutWRte_SF", 23, RegisterDataType.SUNSSF),
)

_READ_WRITE_NAMES = {
    "StorCtl_Mod",
    "VAChaMax",
    "MinRsvPct",
    "OutWRte",
    "InWRte",
    "InOutWRte_RvrtTms",
    "ChaGriSet",
}


def test_model_124_structure_and_access_match_worksheet() -> None:
    """The complete storage layout preserves its worksheet access metadata."""
    assert MODEL_124.model_ids == (124,)
    assert MODEL_124.expected_length == 24
    assert MODEL_124.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.data_type)
        for register in MODEL_124.registers
    ) == _EXPECTED_LAYOUT
    assert all(register.size == 1 for register in MODEL_124.registers)

    read_write = {
        register.name
        for register in MODEL_124.registers
        if register.access is RegisterAccess.READ_WRITE
    }
    read_only = {
        register.name
        for register in MODEL_124.registers
        if register.access is RegisterAccess.READ_ONLY
    }
    assert read_write == _READ_WRITE_NAMES
    assert len(read_write) == 7
    assert len(read_only) == 17


def test_model_124_layout_has_no_gaps_or_overlaps() -> None:
    """The worksheet fields cover every payload word exactly once."""
    occupied_offsets = [
        offset
        for register in MODEL_124.registers
        for offset in range(register.offset, register.offset + register.size)
    ]

    assert occupied_offsets == list(range(24))


def test_model_124_valid_ranges_match_worksheet() -> None:
    """Fixed rates and writable control bounds retain worksheet ranges."""
    ranges = {
        register.name: register.valid_range
        for register in MODEL_124.registers
        if register.valid_range is not None
    }

    assert ranges == {
        "WChaGra": ValueRange(minimum=100, maximum=100),
        "WDisChaGra": ValueRange(minimum=100, maximum=100),
        "OutWRte": ValueRange(minimum=-100, maximum=100),
        "InWRte": ValueRange(minimum=-100, maximum=100),
        "InOutWRte_RvrtTms": ValueRange(minimum=0, maximum=28800),
    }


def test_model_124_scale_links_resolve() -> None:
    """Every exact scale-factor reference names a local register."""
    names = {register.name for register in MODEL_124.registers}
    links = {
        register.name: register.scale_factor
        for register in MODEL_124.registers
        if register.scale_factor is not None
    }

    assert links == {
        "WChaMax": "WChaMax_SF",
        "WChaGra": "WChaDisChaGra_SF",
        "WDisChaGra": "WChaDisChaGra_SF",
        "VAChaMax": "VAChaMax_SF",
        "MinRsvPct": "MinRsvPct_SF",
        "ChaState": "ChaState_SF",
        "StorAval": "StorAval_SF",
        "InBatV": "InBatV_SF",
        "OutWRte": "InOutWRte_SF",
        "InWRte": "InOutWRte_SF",
    }
    assert set(links.values()) <= names
    assert all(
        register.poll_class is PollClass.STATIC
        for register in MODEL_124.registers
        if register.data_type is RegisterDataType.SUNSSF
    )


def test_real_gen24_storage_values_decode() -> None:
    """Observed storage values decode through the generic model decoder."""
    values = decode_model(MODEL_124, _GEN24_PAYLOAD).fixed

    assert values["WChaMax"].value == 10240
    assert values["WChaGra"].value == 100
    assert values["WDisChaGra"].value == 100
    assert values["StorCtl_Mod"].raw == 0
    assert values["StorCtl_Mod"].value == ""
    assert values["MinRsvPct"].value == 7
    assert values["ChaState"].value == 80.3
    assert values["ChaSt"].value == "DISCHARGING"
    assert values["OutWRte"].value == 100
    assert values["InWRte"].value == 100
    assert values["InOutWRte_RvrtTms"].value == 0
    assert values["ChaGriSet"].value == "GRID (Charging from grid enabled)"


def test_real_gen24_invalid_and_valid_scale_factors() -> None:
    """Observed invalid sentinels remain distinct from valid scale factors."""
    values = decode_model(MODEL_124, _GEN24_PAYLOAD).fixed

    for name in ("VAChaMax", "StorAval", "InBatV", "InOutWRte_WinTms"):
        assert values[name].raw == 0xFFFF
        assert values[name].value is None
    assert values["InOutWRte_RmpTms"].raw == 0xFFFF
    assert values["InOutWRte_RmpTms"].value is None
    for name in ("VAChaMax_SF", "StorAval_SF", "InBatV_SF"):
        assert values[name].raw == 0x8000
        assert values[name].value is None
    for name in ("MinRsvPct_SF", "ChaState_SF", "InOutWRte_SF"):
        assert values[name].raw == 0xFFFE
        assert values[name].value == -2


def test_model_124_enum_and_bitfield_metadata() -> None:
    """Storage modes and status meanings retain worksheet labels."""
    registers = {register.name: register for register in MODEL_124.registers}

    assert registers["StorCtl_Mod"].bitfield == {
        0x0001: "CHARGE",
        0x0002: "DISCHARGE",
    }
    assert registers["ChaSt"].enum == {
        1: "OFF",
        2: "EMPTY",
        3: "DISCHARGING",
        4: "CHARGING",
        5: "FULL",
        6: "HOLDING",
        7: "TESTING",
    }
    assert registers["ChaGriSet"].enum == {
        0: "PV (Charging from grid disabled)",
        1: "GRID (Charging from grid enabled)",
    }
