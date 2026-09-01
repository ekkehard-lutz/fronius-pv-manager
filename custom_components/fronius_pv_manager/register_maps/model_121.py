"""Fronius GEN24 SunSpec Model 121 basic settings definition.

Offsets are relative to the 30-register model payload. The worksheet marks all
fields read-only for this device, so this module records no writable access.
"""

from collections.abc import Mapping

from ..models import (
    EntityCategoryHint,
    PhysicalDeviceRole,
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
)
from ._entity_catalog import attach_entities, entity


def _register(
    name: str,
    offset: int,
    data_type: RegisterDataType,
    *,
    unit: str | None = None,
    scale_factor: str | None = None,
    description: str,
    enum: Mapping[int, str] | None = None,
    poll_class: PollClass = PollClass.SLOW,
) -> RegisterDefinition:
    """Create one read-only Model 121 register definition."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=1,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
        enum=enum,
        poll_class=poll_class,
    )


_REGISTERS = (
    _register(
        "WMax",
        0,
        RegisterDataType.UINT16,
        unit="W",
        scale_factor="WMax_SF",
        description="Maximum power output setting.",
    ),
    _register(
        "VRef",
        1,
        RegisterDataType.UINT16,
        unit="V",
        scale_factor="VRef_SF",
        description="Voltage at the point of common coupling.",
    ),
    _register(
        "VRefOfs",
        2,
        RegisterDataType.INT16,
        unit="V",
        scale_factor="VRefOfs_SF",
        description="Voltage offset from the PCC to the inverter.",
    ),
    _register(
        "VMax",
        3,
        RegisterDataType.UINT16,
        unit="V",
        scale_factor="VMinMax_SF",
        description="Maximum voltage setpoint; not supported by Fronius.",
    ),
    _register(
        "VMin",
        4,
        RegisterDataType.UINT16,
        unit="V",
        scale_factor="VMinMax_SF",
        description="Minimum voltage setpoint; not supported by Fronius.",
    ),
    _register(
        "VAMax",
        5,
        RegisterDataType.UINT16,
        unit="VA",
        scale_factor="VAMax_SF",
        description="Maximum apparent power setpoint.",
    ),
    _register(
        "VArMaxQ1",
        6,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArMax_SF",
        description="Maximum reactive power setting in quadrant 1.",
    ),
    _register(
        "VArMaxQ2",
        7,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArMax_SF",
        description="Maximum reactive power setting in quadrant 2.",
    ),
    _register(
        "VArMaxQ3",
        8,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArMax_SF",
        description="Maximum reactive power setting in quadrant 3.",
    ),
    _register(
        "VArMaxQ4",
        9,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArMax_SF",
        description="Maximum reactive power setting in quadrant 4.",
    ),
    _register(
        "WGra",
        10,
        RegisterDataType.UINT16,
        unit="% WMax/sec",
        scale_factor="WGra_SF",
        description="Default active-power ramp rate; not supported by Fronius.",
    ),
    _register(
        "PFMinQ1",
        11,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFMin_SF",
        description="Minimum power factor setting in quadrant 1.",
    ),
    _register(
        "PFMinQ2",
        12,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFMin_SF",
        description="Minimum power factor setting in quadrant 2.",
    ),
    _register(
        "PFMinQ3",
        13,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFMin_SF",
        description="Minimum power factor setting in quadrant 3.",
    ),
    _register(
        "PFMinQ4",
        14,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFMin_SF",
        description="Minimum power factor setting in quadrant 4.",
    ),
    _register(
        "VArAct",
        15,
        RegisterDataType.ENUM16,
        description="VAR action; not supported by Fronius.",
        enum={1: "Switch", 2: "Maintain VAR characterization"},
    ),
    _register(
        "ClcTotVA",
        16,
        RegisterDataType.ENUM16,
        description="Total apparent-power calculation; not supported by Fronius.",
        enum={1: "Vector", 2: "Arithmetic"},
    ),
    _register(
        "MaxRmpRte",
        17,
        RegisterDataType.UINT16,
        unit="% WGra",
        scale_factor="MaxRmpRte_SF",
        description="Maximum ramp percentage; not supported by Fronius.",
    ),
    _register(
        "ECPNomHz",
        18,
        RegisterDataType.UINT16,
        unit="Hz",
        scale_factor="ECPNomHz_SF",
        description="Nominal ECP frequency; not supported by Fronius.",
    ),
    _register(
        "ConnPh",
        19,
        RegisterDataType.ENUM16,
        description="Connected single-phase identity; not supported by Fronius.",
        enum={1: "A", 2: "B", 3: "C"},
    ),
    _register(
        "WMax_SF",
        20,
        RegisterDataType.SUNSSF,
        description="Real-power scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VRef_SF",
        21,
        RegisterDataType.SUNSSF,
        description="PCC voltage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VRefOfs_SF",
        22,
        RegisterDataType.SUNSSF,
        description="Offset-voltage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VMinMax_SF",
        23,
        RegisterDataType.SUNSSF,
        description="Voltage-limit scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VAMax_SF",
        24,
        RegisterDataType.SUNSSF,
        description="Apparent-power scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VArMax_SF",
        25,
        RegisterDataType.SUNSSF,
        description="Reactive-power scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WGra_SF",
        26,
        RegisterDataType.SUNSSF,
        description="Ramp-rate scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "PFMin_SF",
        27,
        RegisterDataType.SUNSSF,
        description="Minimum power-factor scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "MaxRmpRte_SF",
        28,
        RegisterDataType.SUNSSF,
        description="Maximum ramp scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "ECPNomHz_SF",
        29,
        RegisterDataType.SUNSSF,
        description="Nominal frequency scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
)

MODEL_121 = SunSpecModelDefinition(
    model_ids=(121,),
    name="Basic Settings",
    registers=_REGISTERS,
    expected_length=30,
)

MODEL_121 = attach_entities(
    MODEL_121,
    {
        name: entity(
            121,
            name,
            category=EntityCategoryHint.CONFIG,
            enabled=False,
            role=PhysicalDeviceRole.INVERTER,
            translate_enum_values=name in {"VArAct", "ClcTotVA", "ConnPh"},
        )
        for name in (
            "WMax", "VRef", "VRefOfs", "VMax", "VMin", "VAMax", "VArMaxQ1",
            "VArMaxQ2", "VArMaxQ3", "VArMaxQ4", "WGra", "PFMinQ1", "PFMinQ2",
            "PFMinQ3", "PFMinQ4", "VArAct", "ClcTotVA", "MaxRmpRte", "ECPNomHz",
            "ConnPh",
        )
    },
)
