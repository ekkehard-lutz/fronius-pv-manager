"""Fronius Smart Meter SunSpec Model 203 register definition.

Offsets are relative to the 105-register model payload. All fields are
read-only as documented by the Fronius Smart Meter Int+SF worksheet.
"""

from collections.abc import Mapping

from ..models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
)


def _register(
    name: str,
    offset: int,
    size: int,
    data_type: RegisterDataType,
    *,
    unit: str | None = None,
    scale_factor: str | None = None,
    description: str,
    bitfield: Mapping[int, str] | None = None,
    poll_class: PollClass = PollClass.NORMAL,
) -> RegisterDefinition:
    """Create one read-only Model 203 register definition."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
        bitfield=bitfield,
        poll_class=poll_class,
    )


def _instantaneous(
    name: str,
    offset: int,
    unit: str,
    scale_factor: str,
    description: str,
) -> RegisterDefinition:
    """Create one signed instantaneous meter value."""
    return _register(
        name,
        offset,
        1,
        RegisterDataType.INT16,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
    )


def _scale_factor(name: str, offset: int, description: str) -> RegisterDefinition:
    """Create one static meter scale factor."""
    return _register(
        name,
        offset,
        1,
        RegisterDataType.SUNSSF,
        description=description,
        poll_class=PollClass.STATIC,
    )


def _energy(
    name: str,
    offset: int,
    unit: str,
    scale_factor: str,
    description: str,
) -> RegisterDefinition:
    """Create one accumulated 32-bit meter energy value."""
    return _register(
        name,
        offset,
        2,
        RegisterDataType.ACC32,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
        poll_class=PollClass.SLOW,
    )


_REGISTERS = (
    _instantaneous("A", 0, "A", "A_SF", "Total AC current."),
    _instantaneous("AphA", 1, "A", "A_SF", "Phase A current."),
    _instantaneous("AphB", 2, "A", "A_SF", "Phase B current."),
    _instantaneous("AphC", 3, "A", "A_SF", "Phase C current."),
    _scale_factor("A_SF", 4, "Current scale factor."),
    _instantaneous("PhV", 5, "V", "V_SF", "Average line-to-neutral voltage."),
    _instantaneous("PhVphA", 6, "V", "V_SF", "Phase A-to-neutral voltage."),
    _instantaneous("PhVphB", 7, "V", "V_SF", "Phase B-to-neutral voltage."),
    _instantaneous("PhVphC", 8, "V", "V_SF", "Phase C-to-neutral voltage."),
    _instantaneous("PPV", 9, "V", "V_SF", "Average line-to-line voltage."),
    _instantaneous("PhVphAB", 10, "V", "V_SF", "Phase A-to-B voltage."),
    _instantaneous("PhVphBC", 11, "V", "V_SF", "Phase B-to-C voltage."),
    _instantaneous("PhVphCA", 12, "V", "V_SF", "Phase C-to-A voltage."),
    _scale_factor("V_SF", 13, "Voltage scale factor."),
    _instantaneous("Hz", 14, "Hz", "Hz_SF", "AC frequency."),
    _scale_factor("Hz_SF", 15, "Frequency scale factor."),
    _instantaneous("W", 16, "W", "W_SF", "Total real power."),
    _instantaneous("WphA", 17, "W", "W_SF", "Phase A real power."),
    _instantaneous("WphB", 18, "W", "W_SF", "Phase B real power."),
    _instantaneous("WphC", 19, "W", "W_SF", "Phase C real power."),
    _scale_factor("W_SF", 20, "Real-power scale factor."),
    _instantaneous("VA", 21, "VA", "VA_SF", "Total apparent power."),
    _instantaneous("VAphA", 22, "VA", "VA_SF", "Phase A apparent power."),
    _instantaneous("VAphB", 23, "VA", "VA_SF", "Phase B apparent power."),
    _instantaneous("VAphC", 24, "VA", "VA_SF", "Phase C apparent power."),
    _scale_factor("VA_SF", 25, "Apparent-power scale factor."),
    _instantaneous("VAR", 26, "var", "VAR_SF", "Total reactive power."),
    _instantaneous("VARphA", 27, "var", "VAR_SF", "Phase A reactive power."),
    _instantaneous("VARphB", 28, "var", "VAR_SF", "Phase B reactive power."),
    _instantaneous("VARphC", 29, "var", "VAR_SF", "Phase C reactive power."),
    _scale_factor("VAR_SF", 30, "Reactive-power scale factor."),
    _instantaneous("PF", 31, "Pct", "PF_SF", "Total power factor."),
    _instantaneous("PFphA", 32, "Pct", "PF_SF", "Phase A power factor."),
    _instantaneous("PFphB", 33, "Pct", "PF_SF", "Phase B power factor."),
    _instantaneous("PFphC", 34, "Pct", "PF_SF", "Phase C power factor."),
    _scale_factor("PF_SF", 35, "Power-factor scale factor."),
    _energy("TotWhExp", 36, "Wh", "TotWh_SF", "Total real energy exported."),
    _energy(
        "TotWhExpPhA", 38, "Wh", "TotWh_SF", "Phase A real energy exported."
    ),
    _energy(
        "TotWhExpPhB", 40, "Wh", "TotWh_SF", "Phase B real energy exported."
    ),
    _energy(
        "TotWhExpPhC", 42, "Wh", "TotWh_SF", "Phase C real energy exported."
    ),
    _energy("TotWhImp", 44, "Wh", "TotWh_SF", "Total real energy imported."),
    _energy(
        "TotWhImpPhA", 46, "Wh", "TotWh_SF", "Phase A real energy imported."
    ),
    _energy(
        "TotWhImpPhB", 48, "Wh", "TotWh_SF", "Phase B real energy imported."
    ),
    _energy(
        "TotWhImpPhC", 50, "Wh", "TotWh_SF", "Phase C real energy imported."
    ),
    _scale_factor("TotWh_SF", 52, "Real-energy scale factor."),
    _energy(
        "TotVAhExp", 53, "VAh", "TotVAh_SF", "Total apparent energy exported."
    ),
    _energy(
        "TotVAhExpPhA",
        55,
        "VAh",
        "TotVAh_SF",
        "Phase A apparent energy exported.",
    ),
    _energy(
        "TotVAhExpPhB",
        57,
        "VAh",
        "TotVAh_SF",
        "Phase B apparent energy exported.",
    ),
    _energy(
        "TotVAhExpPhC",
        59,
        "VAh",
        "TotVAh_SF",
        "Phase C apparent energy exported.",
    ),
    _energy(
        "TotVAhImp", 61, "VAh", "TotVAh_SF", "Total apparent energy imported."
    ),
    _energy(
        "TotVAhImpPhA",
        63,
        "VAh",
        "TotVAh_SF",
        "Phase A apparent energy imported.",
    ),
    _energy(
        "TotVAhImpPhB",
        65,
        "VAh",
        "TotVAh_SF",
        "Phase B apparent energy imported.",
    ),
    _energy(
        "TotVAhImpPhC",
        67,
        "VAh",
        "TotVAh_SF",
        "Phase C apparent energy imported.",
    ),
    _scale_factor("TotVAh_SF", 69, "Apparent-energy scale factor."),
    _energy(
        "TotVArhImpQ1",
        70,
        "varh",
        "TotVArh_SF",
        "Total reactive energy imported in quadrant 1.",
    ),
    _energy(
        "TotVArhImpQ1PhA", 72, "varh", "TotVArh_SF", "Phase A Q1 energy."
    ),
    _energy(
        "TotVArhImpQ1PhB", 74, "varh", "TotVArh_SF", "Phase B Q1 energy."
    ),
    _energy(
        "TotVArhImpQ1PhC", 76, "varh", "TotVArh_SF", "Phase C Q1 energy."
    ),
    _energy(
        "TotVArhImpQ2",
        78,
        "varh",
        "TotVArh_SF",
        "Total reactive energy imported in quadrant 2.",
    ),
    _energy(
        "TotVArhImpQ2PhA", 80, "varh", "TotVArh_SF", "Phase A Q2 energy."
    ),
    _energy(
        "TotVArhImpQ2PhB", 82, "varh", "TotVArh_SF", "Phase B Q2 energy."
    ),
    _energy(
        "TotVArhImpQ2PhC", 84, "varh", "TotVArh_SF", "Phase C Q2 energy."
    ),
    _energy(
        "TotVArhExpQ3",
        86,
        "varh",
        "TotVArh_SF",
        "Total reactive energy exported in quadrant 3.",
    ),
    _energy(
        "TotVArhExpQ3PhA", 88, "varh", "TotVArh_SF", "Phase A Q3 energy."
    ),
    _energy(
        "TotVArhExpQ3PhB", 90, "varh", "TotVArh_SF", "Phase B Q3 energy."
    ),
    _energy(
        "TotVArhExpQ3PhC", 92, "varh", "TotVArh_SF", "Phase C Q3 energy."
    ),
    _energy(
        "TotVArhExpQ4",
        94,
        "varh",
        "TotVArh_SF",
        "Total reactive energy exported in quadrant 4.",
    ),
    _energy(
        "TotVArhExpQ4PhA", 96, "varh", "TotVArh_SF", "Phase A Q4 energy."
    ),
    _energy(
        "TotVArhExpQ4PhB", 98, "varh", "TotVArh_SF", "Phase B Q4 energy."
    ),
    _energy(
        "TotVArhExpQ4PhC", 100, "varh", "TotVArh_SF", "Phase C Q4 energy."
    ),
    _scale_factor("TotVArh_SF", 102, "Reactive-energy scale factor."),
    _register(
        "Evt",
        103,
        2,
        RegisterDataType.BITFIELD32,
        description="Meter event flags.",
        bitfield={
            0x00000004: "Power Failure",
            0x00000008: "Under Voltage",
            0x00000010: "Low PF",
            0x00000020: "Over Current",
            0x00000040: "Over Voltage",
            0x00000080: "Missing Sensor",
            **{1 << bit: f"Reserved{bit - 7}" for bit in range(8, 16)},
            **{1 << bit: f"OEM{bit - 15:02d}" for bit in range(16, 31)},
        },
        poll_class=PollClass.SLOW,
    ),
)

MODEL_203 = SunSpecModelDefinition(
    model_ids=(203,),
    name="Wye-Connect Three-Phase Meter",
    registers=_REGISTERS,
    expected_length=105,
)
