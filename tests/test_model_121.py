"""Tests for the Fronius GEN24 SunSpec Model 121 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_121

_GEN24_PAYLOAD = [
    0x03E8,
    0x00E6,
    0x0000,
    0xFFFF,
    0xFFFF,
    0x03E8,
    0x02CA,
    0x02CA,
    0xFD36,
    0xFD36,
    0xFFFF,
    0xFD44,
    0x02BC,
    0xFD44,
    0x02BC,
    0xFFFF,
    0xFFFF,
    0xFFFF,
    0xFFFF,
    0xFFFF,
    0x0001,
    0x0000,
    0x0000,
    0x8000,
    0x0001,
    0x0001,
    0x8000,
    0xFFFD,
    0x8000,
    0x8000,
]

_EXPECTED_LAYOUT = (
    ("WMax", 0, RegisterDataType.UINT16),
    ("VRef", 1, RegisterDataType.UINT16),
    ("VRefOfs", 2, RegisterDataType.INT16),
    ("VMax", 3, RegisterDataType.UINT16),
    ("VMin", 4, RegisterDataType.UINT16),
    ("VAMax", 5, RegisterDataType.UINT16),
    ("VArMaxQ1", 6, RegisterDataType.INT16),
    ("VArMaxQ2", 7, RegisterDataType.INT16),
    ("VArMaxQ3", 8, RegisterDataType.INT16),
    ("VArMaxQ4", 9, RegisterDataType.INT16),
    ("WGra", 10, RegisterDataType.UINT16),
    ("PFMinQ1", 11, RegisterDataType.INT16),
    ("PFMinQ2", 12, RegisterDataType.INT16),
    ("PFMinQ3", 13, RegisterDataType.INT16),
    ("PFMinQ4", 14, RegisterDataType.INT16),
    ("VArAct", 15, RegisterDataType.ENUM16),
    ("ClcTotVA", 16, RegisterDataType.ENUM16),
    ("MaxRmpRte", 17, RegisterDataType.UINT16),
    ("ECPNomHz", 18, RegisterDataType.UINT16),
    ("ConnPh", 19, RegisterDataType.ENUM16),
    ("WMax_SF", 20, RegisterDataType.SUNSSF),
    ("VRef_SF", 21, RegisterDataType.SUNSSF),
    ("VRefOfs_SF", 22, RegisterDataType.SUNSSF),
    ("VMinMax_SF", 23, RegisterDataType.SUNSSF),
    ("VAMax_SF", 24, RegisterDataType.SUNSSF),
    ("VArMax_SF", 25, RegisterDataType.SUNSSF),
    ("WGra_SF", 26, RegisterDataType.SUNSSF),
    ("PFMin_SF", 27, RegisterDataType.SUNSSF),
    ("MaxRmpRte_SF", 28, RegisterDataType.SUNSSF),
    ("ECPNomHz_SF", 29, RegisterDataType.SUNSSF),
)


def test_model_121_structure_and_access_match_worksheet() -> None:
    """The complete settings layout retains its worksheet access metadata."""
    assert MODEL_121.model_ids == (121,)
    assert MODEL_121.expected_length == 30
    assert MODEL_121.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.data_type)
        for register in MODEL_121.registers
    ) == _EXPECTED_LAYOUT
    assert all(register.size == 1 for register in MODEL_121.registers)
    assert all(
        register.access is RegisterAccess.READ_ONLY for register in MODEL_121.registers
    )
    assert not any(
        register.access is RegisterAccess.READ_WRITE for register in MODEL_121.registers
    )
    assert all(
        register.offset + register.size <= 30 for register in MODEL_121.registers
    )


def test_model_121_poll_classes_and_scale_links() -> None:
    """Scale factors are static and every named reference resolves locally."""
    names = {register.name for register in MODEL_121.registers}

    assert all(
        register.scale_factor is None or register.scale_factor in names
        for register in MODEL_121.registers
    )
    assert all(
        register.poll_class is PollClass.STATIC
        for register in MODEL_121.registers
        if register.data_type is RegisterDataType.SUNSSF
    )
    assert all(
        register.poll_class is PollClass.SLOW
        for register in MODEL_121.registers
        if register.data_type is not RegisterDataType.SUNSSF
    )


def test_real_gen24_basic_settings_decode() -> None:
    """Observed settings values decode through the generic model decoder."""
    values = decode_model(MODEL_121, _GEN24_PAYLOAD).fixed

    assert values["WMax"].value == 10000
    assert values["VRef"].value == 230
    assert values["VRefOfs"].value == 0
    assert values["VAMax"].value == 10000
    assert values["VArMaxQ1"].value == 7140
    assert values["VArMaxQ2"].value == 7140
    assert values["VArMaxQ3"].value == -7140
    assert values["VArMaxQ4"].value == -7140
    assert values["PFMinQ1"].value == -0.7
    assert values["PFMinQ2"].value == 0.7
    assert values["PFMinQ3"].value == -0.7
    assert values["PFMinQ4"].value == 0.7


def test_real_gen24_invalid_and_valid_scale_factor_values() -> None:
    """Observed invalid sentinels remain distinct from valid negative SUNSSF."""
    values = decode_model(MODEL_121, _GEN24_PAYLOAD).fixed

    for name in ("VMax", "VMin", "WGra", "MaxRmpRte", "ECPNomHz"):
        assert values[name].raw == 0xFFFF
        assert values[name].value is None
    for name in ("VArAct", "ClcTotVA", "ConnPh"):
        assert values[name].raw == 0xFFFF
        assert values[name].value is None
    for name in ("VMinMax_SF", "WGra_SF", "MaxRmpRte_SF", "ECPNomHz_SF"):
        assert values[name].raw == 0x8000
        assert values[name].value is None
    assert values["PFMin_SF"].raw == 0xFFFD
    assert values["PFMin_SF"].value == -3


def test_model_121_enum_metadata() -> None:
    """Worksheet enum meanings remain available despite invalid live values."""
    registers = {register.name: register for register in MODEL_121.registers}

    assert registers["VArAct"].enum == {
        1: "Switch",
        2: "Maintain VAR characterization",
    }
    assert registers["ClcTotVA"].enum == {1: "Vector", 2: "Arithmetic"}
    assert registers["ConnPh"].enum == {1: "A", 2: "B", 3: "C"}
