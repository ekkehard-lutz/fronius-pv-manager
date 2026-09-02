"""Fronius GEN24 SunSpec Model 124 basic storage controls definition.

Writable access is descriptive metadata only. This module provides no Modbus
write path, control sequencing, or Home Assistant behavior.
"""

from collections.abc import Mapping

from ..models import (
    EntityCategoryHint,
    EntityPlatform,
    PhysicalDeviceRole,
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
    ValueRange,
)
from ._entity_catalog import attach_entities, attach_help_texts, entity


def _register(
    name: str,
    offset: int,
    data_type: RegisterDataType,
    access: RegisterAccess,
    *,
    unit: str | None = None,
    scale_factor: str | None = None,
    description: str,
    valid_range: ValueRange | None = None,
    enum: Mapping[int, str] | None = None,
    bitfield: Mapping[int, str] | None = None,
    poll_class: PollClass = PollClass.NORMAL,
) -> RegisterDefinition:
    """Create one Model 124 register definition."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=1,
        data_type=data_type,
        access=access,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
        valid_range=valid_range,
        enum=enum,
        bitfield=bitfield,
        poll_class=poll_class,
    )


_RO = RegisterAccess.READ_ONLY
_RW = RegisterAccess.READ_WRITE

_REGISTERS = (
    _register(
        "WChaMax",
        0,
        RegisterDataType.UINT16,
        _RO,
        unit="W",
        scale_factor="WChaMax_SF",
        description=(
            "Reference maximum charge and discharge power used by InWRte and "
            "OutWRte."
        ),
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WChaGra",
        1,
        RegisterDataType.UINT16,
        _RO,
        unit="% WChaMax/sec",
        scale_factor="WChaDisChaGra_SF",
        description="Maximum charging rate; fixed at 100% by Fronius.",
        valid_range=ValueRange(minimum=100, maximum=100),
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WDisChaGra",
        2,
        RegisterDataType.UINT16,
        _RO,
        unit="% WChaMax/sec",
        scale_factor="WChaDisChaGra_SF",
        description="Maximum discharge rate; fixed at 100% by Fronius.",
        valid_range=ValueRange(minimum=100, maximum=100),
        poll_class=PollClass.STATIC,
    ),
    _register(
        "StorCtl_Mod",
        3,
        RegisterDataType.BITFIELD16,
        _RW,
        description="Active charge and discharge storage-control limits.",
        bitfield={0x0001: "CHARGE", 0x0002: "DISCHARGE"},
        poll_class=PollClass.SLOW,
    ),
    _register(
        "VAChaMax",
        4,
        RegisterDataType.UINT16,
        _RW,
        unit="VA",
        scale_factor="VAChaMax_SF",
        description="Maximum charging apparent-power setpoint.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "MinRsvPct",
        5,
        RegisterDataType.UINT16,
        _RW,
        unit="% WChaMax",
        scale_factor="MinRsvPct_SF",
        description="Minimum storage reserve percentage setpoint.",
        valid_range=ValueRange(minimum=0, maximum=100),
        poll_class=PollClass.SLOW,
    ),
    _register(
        "ChaState",
        6,
        RegisterDataType.UINT16,
        _RO,
        unit="% AhrRtg",
        scale_factor="ChaState_SF",
        description="Currently available energy as a percentage of capacity.",
    ),
    _register(
        "StorAval",
        7,
        RegisterDataType.UINT16,
        _RO,
        unit="AH",
        scale_factor="StorAval_SF",
        description="Available storage above reserve; not supported by Fronius.",
    ),
    _register(
        "InBatV",
        8,
        RegisterDataType.UINT16,
        _RO,
        unit="V",
        scale_factor="InBatV_SF",
        description="Internal battery voltage; not supported by Fronius.",
    ),
    _register(
        "ChaSt",
        9,
        RegisterDataType.ENUM16,
        _RO,
        description="Charge status of the storage device.",
        enum={
            1: "OFF",
            2: "EMPTY",
            3: "DISCHARGING",
            4: "CHARGING",
            5: "FULL",
            6: "HOLDING",
            7: "TESTING",
        },
    ),
    _register(
        "OutWRte",
        10,
        RegisterDataType.INT16,
        _RW,
        unit="% WChaMax",
        scale_factor="InOutWRte_SF",
        description="Maximum discharge rate as a percentage of WChaMax.",
        valid_range=ValueRange(minimum=-100, maximum=100),
        poll_class=PollClass.SLOW,
    ),
    _register(
        "InWRte",
        11,
        RegisterDataType.INT16,
        _RW,
        unit="% WChaMax",
        scale_factor="InOutWRte_SF",
        description="Maximum charge rate as a percentage of WChaMax.",
        valid_range=ValueRange(minimum=-100, maximum=100),
        poll_class=PollClass.SLOW,
    ),
    _register(
        "InOutWRte_WinTms",
        12,
        RegisterDataType.UINT16,
        _RO,
        unit="Secs",
        description="Charge/discharge change window; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "InOutWRte_RvrtTms",
        13,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Timeout period for charge and discharge rate controls.",
        valid_range=ValueRange(minimum=0, maximum=28800),
        poll_class=PollClass.SLOW,
    ),
    _register(
        "InOutWRte_RmpTms",
        14,
        RegisterDataType.UINT16,
        _RO,
        unit="Secs",
        description="Charge/discharge ramp time; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "ChaGriSet",
        15,
        RegisterDataType.ENUM16,
        _RW,
        description="Enable or disable charging from the grid.",
        enum={
            0: "PV (Charging from grid disabled)",
            1: "GRID (Charging from grid enabled)",
        },
        poll_class=PollClass.SLOW,
    ),
    _register(
        "WChaMax_SF",
        16,
        RegisterDataType.SUNSSF,
        _RO,
        description="Maximum-charge power scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WChaDisChaGra_SF",
        17,
        RegisterDataType.SUNSSF,
        _RO,
        description="Maximum charge and discharge rate scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VAChaMax_SF",
        18,
        RegisterDataType.SUNSSF,
        _RO,
        description="Charging apparent-power scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "MinRsvPct_SF",
        19,
        RegisterDataType.SUNSSF,
        _RO,
        description="Minimum-reserve percentage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "ChaState_SF",
        20,
        RegisterDataType.SUNSSF,
        _RO,
        description="Available-energy percentage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "StorAval_SF",
        21,
        RegisterDataType.SUNSSF,
        _RO,
        description="Available-storage scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "InBatV_SF",
        22,
        RegisterDataType.SUNSSF,
        _RO,
        description="Internal battery-voltage scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "InOutWRte_SF",
        23,
        RegisterDataType.SUNSSF,
        _RO,
        description="Charge and discharge rate percentage scale factor.",
        poll_class=PollClass.STATIC,
    ),
)

MODEL_124 = SunSpecModelDefinition(
    model_ids=(124,),
    name="Storage",
    registers=_REGISTERS,
    expected_length=24,
)

_ROLE = PhysicalDeviceRole.STORAGE
MODEL_124 = attach_entities(
    MODEL_124,
    {
        **{
            name: entity(
                124,
                name,
                enabled=name in {"ChaState", "ChaSt"},
                role=_ROLE,
                translate_enum_values=name == "ChaSt",
                device_class="battery" if name == "ChaState" else None,
                state_class="measurement" if name == "ChaState" else None,
                presentation_unit="%" if name == "ChaState" else None,
            )
            for name in (
                "WChaGra", "WDisChaGra", "ChaState", "StorAval", "InBatV",
                "ChaSt",
            )
        },
        "WChaMax": entity(
            124,
            "WChaMax",
            category=EntityCategoryHint.DIAGNOSTIC,
            enabled=False,
            role=_ROLE,
        ),
        **{
            name: entity(
                124,
                name,
                platform=EntityPlatform.NUMBER,
                category=EntityCategoryHint.CONFIG,
                enabled=False,
                role=_ROLE,
                device_class=(
                    "duration" if name == "InOutWRte_RvrtTms" else None
                ),
                state_class=(
                    "measurement" if name == "InOutWRte_RvrtTms" else None
                ),
                presentation_unit=(
                    "%" if name == "MinRsvPct" else
                    "s" if name == "InOutWRte_RvrtTms" else None
                ),
            )
            for name in (
                "MinRsvPct", "OutWRte", "InWRte",
                "InOutWRte_RvrtTms",
            )
        },
        "ChaGriSet": entity(
            124,
            "ChaGriSet",
            platform=EntityPlatform.SELECT,
            category=EntityCategoryHint.CONFIG,
            enabled=False,
            role=_ROLE,
        ),
        "StorCtl_Mod": entity(
            124,
            "StorCtl_Mod",
            platform=EntityPlatform.SELECT,
            category=EntityCategoryHint.CONFIG,
            enabled=False,
            role=_ROLE,
        ),
    },
)
MODEL_124 = attach_help_texts(
    MODEL_124,
    {
        "ChaState": {
            "en": "Available stored energy as a percentage of rated capacity.",
            "de": "Verfügbare gespeicherte Energie in Prozent der Nennkapazität.",
        },
        "ChaGriSet": {
            "en": "Selects whether charging from the grid is permitted.",
            "de": "Legt fest, ob das Laden aus dem Netz zulässig ist.",
        },
    },
)
