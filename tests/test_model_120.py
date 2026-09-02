"""Tests for the Fronius GEN24 SunSpec Model 120 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_120

_GEN24_PAYLOAD = [
    0x0052,
    0x03E8,
    0x0001,
    0x03E8,
    0x0001,
    0x02CA,
    0x02CA,
    0xFD36,
    0xFD36,
    0x0001,
    0x05A9,
    0xFFFE,
    0xFD44,
    0x02BC,
    0xFD44,
    0x02BC,
    0xFFFD,
    0x2B33,
    0x0000,
    0xFFFF,
    0x8000,
    0x2800,
    0x0000,
    0x2800,
    0x0000,
    0x8000,
]

_EXPECTED_LAYOUT = (
    ("DERTyp", 0, RegisterDataType.ENUM16),
    ("WRtg", 1, RegisterDataType.UINT16),
    ("WRtg_SF", 2, RegisterDataType.SUNSSF),
    ("VARtg", 3, RegisterDataType.UINT16),
    ("VARtg_SF", 4, RegisterDataType.SUNSSF),
    ("VArRtgQ1", 5, RegisterDataType.INT16),
    ("VArRtgQ2", 6, RegisterDataType.INT16),
    ("VArRtgQ3", 7, RegisterDataType.INT16),
    ("VArRtgQ4", 8, RegisterDataType.INT16),
    ("VArRtg_SF", 9, RegisterDataType.SUNSSF),
    ("ARtg", 10, RegisterDataType.UINT16),
    ("ARtg_SF", 11, RegisterDataType.SUNSSF),
    ("PFRtgQ1", 12, RegisterDataType.INT16),
    ("PFRtgQ2", 13, RegisterDataType.INT16),
    ("PFRtgQ3", 14, RegisterDataType.INT16),
    ("PFRtgQ4", 15, RegisterDataType.INT16),
    ("PFRtg_SF", 16, RegisterDataType.SUNSSF),
    ("WHRtg", 17, RegisterDataType.UINT16),
    ("WHRtg_SF", 18, RegisterDataType.SUNSSF),
    ("AhrRtg", 19, RegisterDataType.UINT16),
    ("AhrRtg_SF", 20, RegisterDataType.SUNSSF),
    ("MaxChaRte", 21, RegisterDataType.UINT16),
    ("MaxChaRte_SF", 22, RegisterDataType.SUNSSF),
    ("MaxDisChaRte", 23, RegisterDataType.UINT16),
    ("MaxDisChaRte_SF", 24, RegisterDataType.SUNSSF),
    ("Pad", 25, RegisterDataType.UINT16),
)


def test_model_120_structure_matches_fronius_worksheet() -> None:
    """The complete nameplate payload follows the reviewed worksheet layout."""
    assert MODEL_120.model_ids == (120,)
    assert MODEL_120.expected_length == 26
    assert MODEL_120.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.data_type)
        for register in MODEL_120.registers
    ) == _EXPECTED_LAYOUT
    assert all(register.size == 1 for register in MODEL_120.registers)
    assert all(
        register.access is RegisterAccess.READ_ONLY for register in MODEL_120.registers
    )
    assert all(
        register.poll_class is PollClass.STATIC for register in MODEL_120.registers
    )
    assert all(
        register.offset + register.size <= 26 for register in MODEL_120.registers
    )


def test_model_120_scale_factor_links_resolve() -> None:
    """Every worksheet scale-factor link names a fixed model register."""
    names = {register.name for register in MODEL_120.registers}

    assert all(
        register.scale_factor is None or register.scale_factor in names
        for register in MODEL_120.registers
    )


def test_real_gen24_nameplate_values_decode() -> None:
    """Observed nameplate ratings decode through the generic model decoder."""
    values = decode_model(MODEL_120, _GEN24_PAYLOAD).fixed

    assert values["DERTyp"].value == "PV_STOR"
    assert values["WRtg"].value == 10000
    assert values["VARtg"].value == 10000
    assert values["VArRtgQ1"].value == 7140
    assert values["VArRtgQ2"].value == 7140
    assert values["VArRtgQ3"].value == -7140
    assert values["VArRtgQ4"].value == -7140
    assert values["ARtg"].value == 14.49
    assert values["PFRtgQ1"].value == -0.7
    assert values["PFRtgQ2"].value == 0.7
    assert values["PFRtgQ3"].value == -0.7
    assert values["PFRtgQ4"].value == 0.7
    assert values["WHRtg"].value == 11059
    assert values["MaxChaRte"].value == 10240
    assert values["MaxDisChaRte"].value == 10240


def test_real_gen24_scale_factors_and_invalid_values() -> None:
    """Valid negative factors and observed invalid sentinels remain distinct."""
    values = decode_model(MODEL_120, _GEN24_PAYLOAD).fixed

    assert values["WRtg_SF"].value == 1
    assert values["VARtg_SF"].value == 1
    assert values["VArRtg_SF"].value == 1
    assert values["ARtg_SF"].value == -2
    assert values["PFRtg_SF"].value == -3
    assert values["WHRtg_SF"].value == 0
    assert values["AhrRtg"].raw == 0xFFFF
    assert values["AhrRtg"].value is None
    assert values["AhrRtg_SF"].raw == 0x8000
    assert values["AhrRtg_SF"].value is None
    assert values["Pad"].raw == 0x8000
    assert values["Pad"].value == 0x8000


def test_model_120_enum_and_unsupported_metadata() -> None:
    """Worksheet enum and unsupported field notes remain available."""
    registers = {register.name: register for register in MODEL_120.registers}

    assert registers["DERTyp"].enum == {82: "PV_STOR"}
    assert registers["AhrRtg"].description is not None
    assert "not supported" in registers["AhrRtg"].description.lower()
    assert registers["AhrRtg_SF"].description is not None
    assert "not supported" in registers["AhrRtg_SF"].description.lower()
