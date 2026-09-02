"""Tests for the Fronius GEN24 SunSpec Model 122 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_122

_GEN24_PAYLOAD = [
    0x0007,
    0x0007,
    0x0001,
    0x0000,
    0x0000,
    0x0230,
    0x1EF2,
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
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x8000,
    0x8000,
    0xFFFF,
    0x8000,
    0xFFFF,
    0xFFFF,
    0x0000,
    0x0000,
    0x5254,
    0x4300,
    0x0000,
    0x0000,
    0x3227,
    0x49FE,
    0xFFFF,
    0x1064,
    0x0003,
]

_EXPECTED_LAYOUT = (
    ("PVConn", 0, 1, RegisterDataType.BITFIELD16),
    ("StorConn", 1, 1, RegisterDataType.BITFIELD16),
    ("ECPConn", 2, 1, RegisterDataType.BITFIELD16),
    ("ActWh", 3, 4, RegisterDataType.ACC64),
    ("ActVAh", 7, 4, RegisterDataType.ACC64),
    ("ActVArhQ1", 11, 4, RegisterDataType.ACC64),
    ("ActVArhQ2", 15, 4, RegisterDataType.ACC64),
    ("ActVArhQ3", 19, 4, RegisterDataType.ACC64),
    ("ActVArhQ4", 23, 4, RegisterDataType.ACC64),
    ("VArAval", 27, 1, RegisterDataType.INT16),
    ("VArAval_SF", 28, 1, RegisterDataType.SUNSSF),
    ("WAval", 29, 1, RegisterDataType.UINT16),
    ("WAval_SF", 30, 1, RegisterDataType.SUNSSF),
    ("StSetLimMsk", 31, 2, RegisterDataType.BITFIELD32),
    ("StActCtl", 33, 2, RegisterDataType.BITFIELD32),
    ("TmSrc", 35, 4, RegisterDataType.STRING),
    ("Tms", 39, 2, RegisterDataType.UINT32),
    ("RtSt", 41, 1, RegisterDataType.BITFIELD16),
    ("Ris", 42, 1, RegisterDataType.UINT16),
    ("Ris_SF", 43, 1, RegisterDataType.SUNSSF),
)


def test_model_122_structure_and_access_match_worksheet() -> None:
    """The complete status layout preserves its worksheet access metadata."""
    assert MODEL_122.model_ids == (122,)
    assert MODEL_122.expected_length == 44
    assert MODEL_122.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.size, register.data_type)
        for register in MODEL_122.registers
    ) == _EXPECTED_LAYOUT
    assert all(
        register.access is RegisterAccess.READ_ONLY for register in MODEL_122.registers
    )


def test_model_122_layout_has_no_gaps_or_overlaps() -> None:
    """The worksheet fields cover every payload word exactly once."""
    occupied_offsets = [
        offset
        for register in MODEL_122.registers
        for offset in range(register.offset, register.offset + register.size)
    ]

    assert occupied_offsets == list(range(44))


def test_model_122_scale_links_and_poll_classes() -> None:
    """Scale references resolve and stable metadata is polled statically."""
    names = {register.name for register in MODEL_122.registers}
    scale_factors = {
        register.name: register.scale_factor
        for register in MODEL_122.registers
        if register.scale_factor is not None
    }

    assert scale_factors == {
        "VArAval": "VArAval_SF",
        "WAval": "WAval_SF",
        "Ris": "Ris_SF",
    }
    assert set(scale_factors.values()) <= names
    assert all(
        register.poll_class is PollClass.STATIC
        for register in MODEL_122.registers
        if register.data_type is RegisterDataType.SUNSSF
    )


def test_real_gen24_status_values_decode() -> None:
    """Observed status values decode through the generic model decoder."""
    values = decode_model(MODEL_122, _GEN24_PAYLOAD).fixed

    assert values["PVConn"].raw == 0x0007
    assert values["PVConn"].value == "Connected, Available, Operating"
    assert values["StorConn"].value == "Connected, Available, Operating"
    assert values["ECPConn"].value == "Connected"
    assert values["ActWh"].value == 36708082
    assert values["StActCtl"].raw == 0
    assert values["StActCtl"].value == ""
    assert values["TmSrc"].raw == "RTC\x00\x00\x00\x00\x00"
    assert values["TmSrc"].value == "RTC"
    assert values["Tms"].value == 841435646
    assert values["Ris"].value == 4196000
    assert values["Ris_SF"].value == 3


def test_real_gen24_invalid_sentinels_decode_by_field_type() -> None:
    """Observed invalid words follow each field's declared data type."""
    values = decode_model(MODEL_122, _GEN24_PAYLOAD).fixed

    assert values["VArAval"].raw == 0x8000
    assert values["VArAval"].value is None
    assert values["VArAval_SF"].raw == 0x8000
    assert values["VArAval_SF"].value is None
    assert values["WAval"].raw == 0xFFFF
    assert values["WAval"].value is None
    assert values["WAval_SF"].raw == 0x8000
    assert values["WAval_SF"].value is None
    assert values["StSetLimMsk"].raw == 0xFFFFFFFF
    assert values["StSetLimMsk"].value is None
    assert values["RtSt"].raw == 0xFFFF
    assert values["RtSt"].value is None


def test_model_122_bitfield_metadata_matches_documented_masks() -> None:
    """Connection, control, and ride-through masks retain their meanings."""
    registers = {register.name: register for register in MODEL_122.registers}

    assert registers["PVConn"].bitfield == {
        0x0001: "Connected",
        0x0002: "Available",
        0x0004: "Operating",
        0x0008: "Test",
    }
    assert registers["StActCtl"].bitfield[0x00000001] == "FixedW"
    assert registers["StActCtl"].bitfield[0x00000004] == "FixedPF"
    assert registers["StActCtl"].bitfield[0x00004000] == "HFRT"
    assert registers["RtSt"].bitfield == {
        0x0001: "LVRT_ACTIVE",
        0x0002: "HVRT_ACTIVE",
        0x0004: "LFRT_ACTIVE",
        0x0008: "HFRT_ACTIVE",
    }
