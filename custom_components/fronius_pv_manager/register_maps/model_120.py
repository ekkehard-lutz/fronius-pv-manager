"""Fronius GEN24 SunSpec Model 120 nameplate definition.

Offsets are relative to the 26-register model payload and never represent
one-based documentation registers or zero-based transport addresses.
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
) -> RegisterDefinition:
    """Create one static read-only Model 120 register definition."""
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
        poll_class=PollClass.STATIC,
    )


_REGISTERS = (
    _register(
        "DERTyp",
        0,
        RegisterDataType.ENUM16,
        description="Type of distributed energy resource device.",
        enum={82: "PV_STOR"},
    ),
    _register(
        "WRtg",
        1,
        RegisterDataType.UINT16,
        unit="W",
        scale_factor="WRtg_SF",
        description="Continuous inverter power output capability.",
    ),
    _register(
        "WRtg_SF",
        2,
        RegisterDataType.SUNSSF,
        description="Continuous power rating scale factor.",
    ),
    _register(
        "VARtg",
        3,
        RegisterDataType.UINT16,
        unit="VA",
        scale_factor="VARtg_SF",
        description="Continuous inverter volt-ampere capability.",
    ),
    _register(
        "VARtg_SF",
        4,
        RegisterDataType.SUNSSF,
        description="Volt-ampere rating scale factor.",
    ),
    _register(
        "VArRtgQ1",
        5,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArRtg_SF",
        description="Continuous VAR capability in quadrant 1.",
    ),
    _register(
        "VArRtgQ2",
        6,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArRtg_SF",
        description="Continuous VAR capability in quadrant 2.",
    ),
    _register(
        "VArRtgQ3",
        7,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArRtg_SF",
        description="Continuous VAR capability in quadrant 3.",
    ),
    _register(
        "VArRtgQ4",
        8,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArRtg_SF",
        description="Continuous VAR capability in quadrant 4.",
    ),
    _register(
        "VArRtg_SF",
        9,
        RegisterDataType.SUNSSF,
        description="VAR rating scale factor.",
    ),
    _register(
        "ARtg",
        10,
        RegisterDataType.UINT16,
        unit="A",
        scale_factor="ARtg_SF",
        description="Maximum RMS AC current capability.",
    ),
    _register(
        "ARtg_SF",
        11,
        RegisterDataType.SUNSSF,
        description="AC current rating scale factor.",
    ),
    _register(
        "PFRtgQ1",
        12,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFRtg_SF",
        description="Minimum power factor capability in quadrant 1.",
    ),
    _register(
        "PFRtgQ2",
        13,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFRtg_SF",
        description="Minimum power factor capability in quadrant 2.",
    ),
    _register(
        "PFRtgQ3",
        14,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFRtg_SF",
        description="Minimum power factor capability in quadrant 3.",
    ),
    _register(
        "PFRtgQ4",
        15,
        RegisterDataType.INT16,
        unit="cos()",
        scale_factor="PFRtg_SF",
        description="Minimum power factor capability in quadrant 4.",
    ),
    _register(
        "PFRtg_SF",
        16,
        RegisterDataType.SUNSSF,
        description="Power factor rating scale factor.",
    ),
    _register(
        "WHRtg",
        17,
        RegisterDataType.UINT16,
        unit="Wh",
        scale_factor="WHRtg_SF",
        description="Nominal storage energy rating.",
    ),
    _register(
        "WHRtg_SF",
        18,
        RegisterDataType.SUNSSF,
        description="Storage energy rating scale factor.",
    ),
    _register(
        "AhrRtg",
        19,
        RegisterDataType.UINT16,
        unit="AH",
        scale_factor="AhrRtg_SF",
        description="Usable battery capacity; not supported by Fronius.",
    ),
    _register(
        "AhrRtg_SF",
        20,
        RegisterDataType.SUNSSF,
        description="Amp-hour rating scale factor; not supported by Fronius.",
    ),
    _register(
        "MaxChaRte",
        21,
        RegisterDataType.UINT16,
        unit="W",
        scale_factor="MaxChaRte_SF",
        description="Maximum energy transfer rate into storage.",
    ),
    _register(
        "MaxChaRte_SF",
        22,
        RegisterDataType.SUNSSF,
        description="Maximum charge rate scale factor.",
    ),
    _register(
        "MaxDisChaRte",
        23,
        RegisterDataType.UINT16,
        unit="W",
        scale_factor="MaxDisChaRte_SF",
        description="Maximum energy transfer rate out of storage.",
    ),
    _register(
        "MaxDisChaRte_SF",
        24,
        RegisterDataType.SUNSSF,
        description="Maximum discharge rate scale factor.",
    ),
    _register(
        "Pad",
        25,
        RegisterDataType.UINT16,
        description="Pad register represented as an unsigned transport word.",
    ),
)

MODEL_120 = SunSpecModelDefinition(
    model_ids=(120,),
    name="Nameplate",
    registers=_REGISTERS,
    expected_length=26,
)

MODEL_120 = attach_entities(
    MODEL_120,
    {
        name: entity(
            120,
            name,
            category=EntityCategoryHint.DIAGNOSTIC,
            enabled=False,
            role=PhysicalDeviceRole.INVERTER,
            translate_enum_values=name == "DERTyp",
        )
        for name in (
            "DERTyp", "WRtg", "VARtg", "VArRtgQ1", "VArRtgQ2", "VArRtgQ3",
            "VArRtgQ4", "ARtg", "PFRtgQ1", "PFRtgQ2", "PFRtgQ3", "PFRtgQ4",
            "WHRtg", "AhrRtg", "MaxChaRte", "MaxDisChaRte",
        )
    },
)
