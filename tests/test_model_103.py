"""Tests for the SunSpec Model 103 definition and observed GEN24 payload."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import RegisterDataType
from custom_components.fronius_pv_manager.register_maps import MODEL_103

_GEN24_PAYLOAD = [
    0x3850,
    0x12C4,
    0x12C9,
    0x12C3,
    0xFFFC,
    0x0FA4,
    0x0FA8,
    0x0FAC,
    0x0907,
    0x0909,
    0x090E,
    0xFFFF,
    0x0D1A,
    0xFFFF,
    0x138B,
    0xFFFE,
    0x0D08,
    0xFFFF,
    0xEE09,
    0xFFFC,
    0x03E8,
    0xFFFF,
    0xDACB,
    0xDA71,
    0xFFFE,
    0xFFFF,
    0x8000,
    0xFFFF,
    0x8000,
    0x0ED6,
    0xFFFF,
    0x020A,
    0x8000,
    0x8000,
    0x8000,
    0xFFFF,
    0x0004,
    0x0004,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0x0000,
    0xFFFF,
    0xFFFF,
    0xFFFF,
    0xFFFF,
]

_EXPECTED_LAYOUT = (
    ("A", 0, 1, RegisterDataType.UINT16),
    ("AphA", 1, 1, RegisterDataType.UINT16),
    ("AphB", 2, 1, RegisterDataType.UINT16),
    ("AphC", 3, 1, RegisterDataType.UINT16),
    ("A_SF", 4, 1, RegisterDataType.SUNSSF),
    ("PPVphAB", 5, 1, RegisterDataType.UINT16),
    ("PPVphBC", 6, 1, RegisterDataType.UINT16),
    ("PPVphCA", 7, 1, RegisterDataType.UINT16),
    ("PhVphA", 8, 1, RegisterDataType.UINT16),
    ("PhVphB", 9, 1, RegisterDataType.UINT16),
    ("PhVphC", 10, 1, RegisterDataType.UINT16),
    ("V_SF", 11, 1, RegisterDataType.SUNSSF),
    ("W", 12, 1, RegisterDataType.INT16),
    ("W_SF", 13, 1, RegisterDataType.SUNSSF),
    ("Hz", 14, 1, RegisterDataType.UINT16),
    ("Hz_SF", 15, 1, RegisterDataType.SUNSSF),
    ("VA", 16, 1, RegisterDataType.INT16),
    ("VA_SF", 17, 1, RegisterDataType.SUNSSF),
    ("VAr", 18, 1, RegisterDataType.INT16),
    ("VAr_SF", 19, 1, RegisterDataType.SUNSSF),
    ("PF", 20, 1, RegisterDataType.INT16),
    ("PF_SF", 21, 1, RegisterDataType.SUNSSF),
    ("WH", 22, 2, RegisterDataType.ACC32),
    ("WH_SF", 24, 1, RegisterDataType.SUNSSF),
    ("DCA", 25, 1, RegisterDataType.UINT16),
    ("DCA_SF", 26, 1, RegisterDataType.SUNSSF),
    ("DCV", 27, 1, RegisterDataType.UINT16),
    ("DCV_SF", 28, 1, RegisterDataType.SUNSSF),
    ("DCW", 29, 1, RegisterDataType.INT16),
    ("DCW_SF", 30, 1, RegisterDataType.SUNSSF),
    ("TmpCab", 31, 1, RegisterDataType.INT16),
    ("TmpSnk", 32, 1, RegisterDataType.INT16),
    ("TmpTrns", 33, 1, RegisterDataType.INT16),
    ("TmpOt", 34, 1, RegisterDataType.INT16),
    ("Tmp_SF", 35, 1, RegisterDataType.SUNSSF),
    ("St", 36, 1, RegisterDataType.ENUM16),
    ("StVnd", 37, 1, RegisterDataType.ENUM16),
    ("Evt1", 38, 2, RegisterDataType.BITFIELD32),
    ("Evt2", 40, 2, RegisterDataType.BITFIELD32),
    ("EvtVnd1", 42, 2, RegisterDataType.BITFIELD32),
    ("EvtVnd2", 44, 2, RegisterDataType.BITFIELD32),
    ("EvtVnd3", 46, 2, RegisterDataType.BITFIELD32),
    ("EvtVnd4", 48, 2, RegisterDataType.BITFIELD32),
)


def test_model_103_structure_matches_fronius_worksheet() -> None:
    """The complete payload layout matches the reviewed Int+SF worksheet."""
    assert MODEL_103.model_ids == (103,)
    assert MODEL_103.expected_length == 50
    assert MODEL_103.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.size, register.data_type)
        for register in MODEL_103.registers
    ) == _EXPECTED_LAYOUT
    assert all(
        register.offset + register.size <= 50 for register in MODEL_103.registers
    )
    assert all(
        current.offset + current.size <= following.offset
        for current, following in zip(
            MODEL_103.registers, MODEL_103.registers[1:], strict=False
        )
    )


def test_model_103_scale_factor_references_exist() -> None:
    """Every named scale factor resolves to a fixed register definition."""
    names = {register.name for register in MODEL_103.registers}

    assert all(
        register.scale_factor is None or register.scale_factor in names
        for register in MODEL_103.registers
    )


def test_real_gen24_payload_decodes_electrical_values() -> None:
    """The observed 50-word payload decodes through generic model logic."""
    values = decode_model(MODEL_103, _GEN24_PAYLOAD).fixed

    assert values["A"].value == 1.4416
    assert values["AphA"].value == 0.4804
    assert values["AphB"].value == 0.4809
    assert values["AphC"].value == 0.4803
    assert values["PPVphAB"].value == 400.4
    assert values["PPVphBC"].value == 400.8
    assert values["PPVphCA"].value == 401.2
    assert values["PhVphA"].value == 231.1
    assert values["PhVphB"].value == 231.3
    assert values["PhVphC"].value == 231.8
    assert values["W"].value == 335.4
    assert values["Hz"].value == 50.03
    assert values["VA"].value == 333.6
    assert values["VAr"].value == -0.4599
    assert values["PF"].value == 100
    assert values["WH"].value == 36707928.17
    assert values["DCA"].value is None
    assert values["DCV"].value is None
    assert values["DCW"].value == 379.8
    assert values["St"].value == "MPPT"
    assert values["StVnd"].value == "MPPT"


def test_real_gen24_invalid_signed_values_and_scale_factors() -> None:
    """Observed signed sentinels are invalid while valid SUNSSF negatives remain."""
    values = decode_model(MODEL_103, _GEN24_PAYLOAD).fixed

    assert values["TmpCab"].value == 52.2
    assert values["TmpSnk"].raw == 0x8000
    assert values["TmpSnk"].value is None
    assert values["TmpTrns"].raw == 0x8000
    assert values["TmpTrns"].value is None
    assert values["TmpOt"].raw == 0x8000
    assert values["TmpOt"].value is None
    assert values["A_SF"].value == -4
    assert values["Hz_SF"].value == -2
    assert values["V_SF"].value == -1
    assert values["DCA_SF"].value is None
    assert values["DCV_SF"].value is None


def test_model_103_enum_and_event_metadata() -> None:
    """State codes and worksheet-defined vendor severity masks are retained."""
    registers = {register.name: register for register in MODEL_103.registers}

    assert registers["St"].enum is not None
    assert registers["St"].enum[4] == "MPPT"
    assert registers["StVnd"].enum == registers["St"].enum
    assert registers["EvtVnd1"].bitfield == {
        0x00000001: "Error",
        0x00000002: "Warning",
        0x00000004: "Info",
    }
    assert registers["EvtVnd2"].bitfield == registers["EvtVnd1"].bitfield
