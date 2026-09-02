"""Tests for the Fronius Smart Meter SunSpec Model 203 definition."""

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_203

_GEN24_SMART_METER_PAYLOAD = [
    0x0154, 0x26E8, 0xEEC6, 0xEBA6, 0xFFFC, 0x08F9, 0x08F1,
    0x08FC, 0x08FD, 0x0F8A, 0x0F72, 0x0FA4, 0x0F87, 0xFFFF,
    0x1388, 0xFFFE, 0x0172, 0x2102, 0xE566, 0xF8BC, 0xFFFE,
    0x5D02, 0x2972, 0x1EAA, 0x14DC, 0xFFFE, 0xC842, 0xEADE,
    0xF0E2, 0xEC8C, 0xFFFE, 0x0008, 0x0181, 0xFD67, 0xFF69,
    0xFFFF, 0x2404, 0xCF58, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x187C, 0x442C, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0xFFFE, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x8000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x8000, 0x0000, 0x0000,
]

_EXPECTED_LAYOUT = (
    ("A", 0, 1, RegisterDataType.INT16),
    ("AphA", 1, 1, RegisterDataType.INT16),
    ("AphB", 2, 1, RegisterDataType.INT16),
    ("AphC", 3, 1, RegisterDataType.INT16),
    ("A_SF", 4, 1, RegisterDataType.SUNSSF),
    ("PhV", 5, 1, RegisterDataType.INT16),
    ("PhVphA", 6, 1, RegisterDataType.INT16),
    ("PhVphB", 7, 1, RegisterDataType.INT16),
    ("PhVphC", 8, 1, RegisterDataType.INT16),
    ("PPV", 9, 1, RegisterDataType.INT16),
    ("PhVphAB", 10, 1, RegisterDataType.INT16),
    ("PhVphBC", 11, 1, RegisterDataType.INT16),
    ("PhVphCA", 12, 1, RegisterDataType.INT16),
    ("V_SF", 13, 1, RegisterDataType.SUNSSF),
    ("Hz", 14, 1, RegisterDataType.INT16),
    ("Hz_SF", 15, 1, RegisterDataType.SUNSSF),
    ("W", 16, 1, RegisterDataType.INT16),
    ("WphA", 17, 1, RegisterDataType.INT16),
    ("WphB", 18, 1, RegisterDataType.INT16),
    ("WphC", 19, 1, RegisterDataType.INT16),
    ("W_SF", 20, 1, RegisterDataType.SUNSSF),
    ("VA", 21, 1, RegisterDataType.INT16),
    ("VAphA", 22, 1, RegisterDataType.INT16),
    ("VAphB", 23, 1, RegisterDataType.INT16),
    ("VAphC", 24, 1, RegisterDataType.INT16),
    ("VA_SF", 25, 1, RegisterDataType.SUNSSF),
    ("VAR", 26, 1, RegisterDataType.INT16),
    ("VARphA", 27, 1, RegisterDataType.INT16),
    ("VARphB", 28, 1, RegisterDataType.INT16),
    ("VARphC", 29, 1, RegisterDataType.INT16),
    ("VAR_SF", 30, 1, RegisterDataType.SUNSSF),
    ("PF", 31, 1, RegisterDataType.INT16),
    ("PFphA", 32, 1, RegisterDataType.INT16),
    ("PFphB", 33, 1, RegisterDataType.INT16),
    ("PFphC", 34, 1, RegisterDataType.INT16),
    ("PF_SF", 35, 1, RegisterDataType.SUNSSF),
    ("TotWhExp", 36, 2, RegisterDataType.ACC32),
    ("TotWhExpPhA", 38, 2, RegisterDataType.ACC32),
    ("TotWhExpPhB", 40, 2, RegisterDataType.ACC32),
    ("TotWhExpPhC", 42, 2, RegisterDataType.ACC32),
    ("TotWhImp", 44, 2, RegisterDataType.ACC32),
    ("TotWhImpPhA", 46, 2, RegisterDataType.ACC32),
    ("TotWhImpPhB", 48, 2, RegisterDataType.ACC32),
    ("TotWhImpPhC", 50, 2, RegisterDataType.ACC32),
    ("TotWh_SF", 52, 1, RegisterDataType.SUNSSF),
    ("TotVAhExp", 53, 2, RegisterDataType.ACC32),
    ("TotVAhExpPhA", 55, 2, RegisterDataType.ACC32),
    ("TotVAhExpPhB", 57, 2, RegisterDataType.ACC32),
    ("TotVAhExpPhC", 59, 2, RegisterDataType.ACC32),
    ("TotVAhImp", 61, 2, RegisterDataType.ACC32),
    ("TotVAhImpPhA", 63, 2, RegisterDataType.ACC32),
    ("TotVAhImpPhB", 65, 2, RegisterDataType.ACC32),
    ("TotVAhImpPhC", 67, 2, RegisterDataType.ACC32),
    ("TotVAh_SF", 69, 1, RegisterDataType.SUNSSF),
    ("TotVArhImpQ1", 70, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ1PhA", 72, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ1PhB", 74, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ1PhC", 76, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ2", 78, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ2PhA", 80, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ2PhB", 82, 2, RegisterDataType.ACC32),
    ("TotVArhImpQ2PhC", 84, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ3", 86, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ3PhA", 88, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ3PhB", 90, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ3PhC", 92, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ4", 94, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ4PhA", 96, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ4PhB", 98, 2, RegisterDataType.ACC32),
    ("TotVArhExpQ4PhC", 100, 2, RegisterDataType.ACC32),
    ("TotVArh_SF", 102, 1, RegisterDataType.SUNSSF),
    ("Evt", 103, 2, RegisterDataType.BITFIELD32),
)


def test_model_203_structure_is_complete_and_read_only() -> None:
    """The entire worksheet layout and access metadata are preserved."""
    assert len(_GEN24_SMART_METER_PAYLOAD) == 105
    assert MODEL_203.model_ids == (203,)
    assert MODEL_203.expected_length == 105
    assert MODEL_203.repeating_blocks == ()
    assert tuple(
        (register.name, register.offset, register.size, register.data_type)
        for register in MODEL_203.registers
    ) == _EXPECTED_LAYOUT
    assert all(
        register.access is RegisterAccess.READ_ONLY for register in MODEL_203.registers
    )


def test_model_203_layout_is_contiguous_and_overlap_free() -> None:
    """Every one of the 105 payload words is covered exactly once."""
    occupied = [
        offset
        for register in MODEL_203.registers
        for offset in range(register.offset, register.offset + register.size)
    ]

    assert occupied == list(range(105))


def test_model_203_scale_links_resolve() -> None:
    """Every worksheet scale-factor link names a local static register."""
    names = {register.name for register in MODEL_203.registers}
    links = {
        register.scale_factor
        for register in MODEL_203.registers
        if register.scale_factor is not None
    }

    assert links == {
        "A_SF", "V_SF", "Hz_SF", "W_SF", "VA_SF", "VAR_SF", "PF_SF",
        "TotWh_SF", "TotVAh_SF", "TotVArh_SF",
    }
    assert links <= names
    assert all(
        register.poll_class is PollClass.STATIC
        for register in MODEL_203.registers
        if register.data_type is RegisterDataType.SUNSSF
    )


def test_real_smart_meter_instantaneous_values_decode() -> None:
    """Observed phase and total measurements decode with worksheet scaling."""
    values = decode_model(MODEL_203, _GEN24_SMART_METER_PAYLOAD).fixed

    assert values["A"].value == 0.034
    assert values["AphA"].value == 0.996
    assert values["AphB"].value == -0.441
    assert values["AphC"].value == -0.521
    assert values["PhV"].value == 229.7
    assert values["PhVphA"].value == 228.9
    assert values["PhVphB"].value == 230.0
    assert values["PhVphC"].value == 230.1
    assert values["PhVphAB"].value == 395.4
    assert values["PhVphBC"].value == 400.4
    assert values["PhVphCA"].value == 397.5
    assert values["Hz"].value == 50
    assert values["W"].value == 3.7
    assert values["WphA"].value == 84.5
    assert values["WphB"].value == -68.1
    assert values["WphC"].value == -18.6
    assert values["VA"].value == 238.1
    assert values["VAR"].value == -142.7
    assert values["PF"].value == 0.8


def test_real_smart_meter_signed_words_decode_as_int16() -> None:
    """Unsigned inspector words retain their signed worksheet semantics."""
    values = decode_model(MODEL_203, _GEN24_SMART_METER_PAYLOAD).fixed

    assert values["AphB"].raw == 0xEEC6
    assert values["AphB"].value == -0.441
    assert values["WphB"].raw == 0xE566
    assert values["WphB"].value == -68.1
    assert values["VAR"].raw == 0xC842
    assert values["VAR"].value == -142.7
    assert values["PFphB"].raw == 0xFD67
    assert values["PFphB"].value == -66.5


def test_real_smart_meter_accumulated_energy_decode() -> None:
    """Real-energy counters retain ACC32 semantics and scale correctly."""
    values = decode_model(MODEL_203, _GEN24_SMART_METER_PAYLOAD).fixed

    assert values["TotWhExp"].raw == 604295000
    assert values["TotWhExp"].value == 6042950
    assert values["TotWhImp"].raw == 410797100
    assert values["TotWhImp"].value == 4107971
    assert values["TotWhExpPhA"].raw == 0
    assert values["TotWhExpPhA"].value is None


def test_real_smart_meter_scale_factors_and_invalid_values() -> None:
    """Valid negative scaling remains distinct from invalid SUNSSF values."""
    values = decode_model(MODEL_203, _GEN24_SMART_METER_PAYLOAD).fixed

    assert values["A_SF"].raw == 0xFFFC
    assert values["A_SF"].value == -4
    assert values["V_SF"].raw == 0xFFFF
    assert values["V_SF"].value == -1
    for name in ("Hz_SF", "W_SF", "VA_SF", "VAR_SF", "TotWh_SF"):
        assert values[name].raw == 0xFFFE
        assert values[name].value == -2
    assert values["PF_SF"].raw == 0xFFFF
    assert values["PF_SF"].value == -1
    assert values["TotVAh_SF"].raw == 0x8000
    assert values["TotVAh_SF"].value is None
    assert values["TotVArh_SF"].raw == 0x8000
    assert values["TotVArh_SF"].value is None


def test_model_203_event_bitfield_metadata() -> None:
    """Documented meter event masks remain available to the generic codec."""
    values = decode_model(MODEL_203, _GEN24_SMART_METER_PAYLOAD).fixed
    event = next(register for register in MODEL_203.registers if register.name == "Evt")

    assert event.bitfield[0x00000004] == "Power Failure"
    assert event.bitfield[0x00000008] == "Under Voltage"
    assert event.bitfield[0x00000080] == "Missing Sensor"
    assert event.bitfield[0x40000000] == "OEM15"
    assert values["Evt"].raw == 0
    assert values["Evt"].value == ""
