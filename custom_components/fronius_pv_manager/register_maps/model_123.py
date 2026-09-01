"""Fronius GEN24 SunSpec Model 123 immediate controls definition.

This module records worksheet write capability as metadata only. It provides
no transport writes, control sequencing, or Home Assistant entities.
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
from ._entity_catalog import attach_entities, entity


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
    poll_class: PollClass = PollClass.SLOW,
) -> RegisterDefinition:
    """Create one Model 123 register definition."""
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
_DISABLED_ENABLED = {0: "Disabled", 1: "Enabled"}
_WINDOW_RANGE = ValueRange(minimum=0, maximum=300)
_REVERSION_RANGE = ValueRange(minimum=0, maximum=28800)

_REGISTERS = (
    _register(
        "Conn_WinTms",
        0,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Time window for connect or disconnect.",
    ),
    _register(
        "Conn_RvrtTms",
        1,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Timeout period for connect or disconnect.",
    ),
    _register(
        "Conn",
        2,
        RegisterDataType.BITFIELD16,
        _RW,
        description="Connection control.",
        bitfield={0x0001: "Connected"},
    ),
    _register(
        "WMaxLimPct",
        3,
        RegisterDataType.UINT16,
        _RW,
        unit="% WMax",
        scale_factor="WMaxLimPct_SF",
        description="Set power output to the specified level.",
    ),
    _register(
        "WMaxLimPct_WinTms",
        4,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Time window for power-limit change.",
        valid_range=_WINDOW_RANGE,
    ),
    _register(
        "WMaxLimPct_RvrtTms",
        5,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Timeout period for the power limit.",
        valid_range=_REVERSION_RANGE,
    ),
    _register(
        "WMaxLimPct_RmpTms",
        6,
        RegisterDataType.UINT16,
        _RO,
        unit="Secs",
        description="Power-limit ramp time; not supported by Fronius.",
    ),
    _register(
        "WMaxLim_Ena",
        7,
        RegisterDataType.ENUM16,
        _RW,
        description="Power-limit enable or disable control.",
        enum=_DISABLED_ENABLED,
    ),
    _register(
        "OutPFSet",
        8,
        RegisterDataType.INT16,
        _RW,
        unit="cos()",
        scale_factor="OutPFSet_SF",
        description="Set power factor to a specific value.",
    ),
    _register(
        "OutPFSet_WinTms",
        9,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Time window for power-factor change.",
        valid_range=_WINDOW_RANGE,
    ),
    _register(
        "OutPFSet_RvrtTms",
        10,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Timeout period for power factor.",
        valid_range=_REVERSION_RANGE,
    ),
    _register(
        "OutPFSet_RmpTms",
        11,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Power-factor ramp time; not supported by Fronius.",
    ),
    _register(
        "OutPFSet_Ena",
        12,
        RegisterDataType.ENUM16,
        _RW,
        description="Fixed power-factor enable or disable control.",
        enum=_DISABLED_ENABLED,
    ),
    _register(
        "VArWMaxPct",
        13,
        RegisterDataType.INT16,
        _RO,
        unit="% WMax",
        scale_factor="VArPct_SF",
        description="Reactive power as a percentage of WMax; not supported by Fronius.",
    ),
    _register(
        "VArMaxPct",
        14,
        RegisterDataType.INT16,
        _RW,
        unit="% VArMax",
        scale_factor="VArPct_SF",
        description="Reactive power as a percentage of VArMax.",
    ),
    _register(
        "VArAvalPct",
        15,
        RegisterDataType.INT16,
        _RO,
        unit="% VArAval",
        scale_factor="VArPct_SF",
        description=(
            "Reactive power as a percentage of VArAval; not supported by Fronius."
        ),
    ),
    _register(
        "VArPct_WinTms",
        16,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Time window for VAR-limit change.",
        valid_range=_WINDOW_RANGE,
    ),
    _register(
        "VArPct_RvrtTms",
        17,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="Timeout period for the VAR limit.",
        valid_range=_REVERSION_RANGE,
    ),
    _register(
        "VArPct_RmpTms",
        18,
        RegisterDataType.UINT16,
        _RW,
        unit="Secs",
        description="VAR-limit ramp time; not supported by Fronius.",
    ),
    _register(
        "VArPct_Mod",
        19,
        RegisterDataType.ENUM16,
        _RO,
        description="VAR limit mode.",
        enum={2: "VAR limit as a % of VArMax"},
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VArPct_Ena",
        20,
        RegisterDataType.ENUM16,
        _RW,
        description="Fixed VAR enable or disable control.",
        enum=_DISABLED_ENABLED,
    ),
    _register(
        "WMaxLimPct_SF",
        21,
        RegisterDataType.SUNSSF,
        _RO,
        description="Power-output percentage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "OutPFSet_SF",
        22,
        RegisterDataType.SUNSSF,
        _RO,
        description="Power-factor scale factor.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VArPct_SF",
        23,
        RegisterDataType.SUNSSF,
        _RO,
        description="Reactive-power percentage scale factor.",
        poll_class=PollClass.STATIC,
    ),
)

MODEL_123 = SunSpecModelDefinition(
    model_ids=(123,),
    name="Immediate Controls",
    registers=_REGISTERS,
    expected_length=24,
)

_NUMBER_CONTROLS = {
    "Conn_WinTms", "Conn_RvrtTms", "WMaxLimPct", "WMaxLimPct_WinTms",
    "WMaxLimPct_RvrtTms", "OutPFSet", "OutPFSet_WinTms", "OutPFSet_RvrtTms",
    "OutPFSet_RmpTms", "VArMaxPct", "VArPct_WinTms", "VArPct_RvrtTms",
    "VArPct_RmpTms",
}
_SWITCH_CONTROLS = {"Conn", "WMaxLim_Ena", "OutPFSet_Ena", "VArPct_Ena"}
MODEL_123 = attach_entities(
    MODEL_123,
    {
        **{
            name: entity(
                123,
                name,
                platform=EntityPlatform.NUMBER,
                category=EntityCategoryHint.CONFIG,
                enabled=False,
                role=PhysicalDeviceRole.INVERTER,
                device_class="duration" if name.endswith("Tms") else None,
                state_class="measurement" if name.endswith("Tms") else None,
                presentation_unit="s" if name.endswith("Tms") else None,
            )
            for name in _NUMBER_CONTROLS
        },
        **{
            name: entity(
                123,
                name,
                platform=EntityPlatform.SWITCH,
                category=EntityCategoryHint.CONFIG,
                enabled=False,
                role=PhysicalDeviceRole.INVERTER,
            )
            for name in _SWITCH_CONTROLS
        },
        **{
            name: entity(
                123,
                name,
                category=EntityCategoryHint.CONFIG,
                enabled=False,
                role=PhysicalDeviceRole.INVERTER,
                translate_enum_values=name == "VArPct_Mod",
                device_class=(
                    "duration" if name == "WMaxLimPct_RmpTms" else None
                ),
                state_class=(
                    "measurement" if name == "WMaxLimPct_RmpTms" else None
                ),
                presentation_unit="s" if name == "WMaxLimPct_RmpTms" else None,
            )
            for name in (
                "WMaxLimPct_RmpTms", "VArWMaxPct", "VArAvalPct", "VArPct_Mod"
            )
        },
    },
)
