"""Fronius GEN24 SunSpec Model 122 measurements and status definition.

Offsets are relative to the 44-register model payload. The Fronius worksheet
marks every field read-only and identifies unsupported fields in their
descriptions.
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
    size: int,
    data_type: RegisterDataType,
    *,
    unit: str | None = None,
    scale_factor: str | None = None,
    description: str,
    bitfield: Mapping[int, str] | None = None,
    poll_class: PollClass = PollClass.NORMAL,
) -> RegisterDefinition:
    """Create one read-only Model 122 register definition."""
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


_CONNECTION_STATUS = {
    0x0001: "Connected",
    0x0002: "Available",
    0x0004: "Operating",
    0x0008: "Test",
}

_REGISTERS = (
    _register(
        "PVConn",
        0,
        1,
        RegisterDataType.BITFIELD16,
        description="PV inverter present and available status.",
        bitfield=_CONNECTION_STATUS,
    ),
    _register(
        "StorConn",
        1,
        1,
        RegisterDataType.BITFIELD16,
        description="Storage inverter present and available status.",
        bitfield=_CONNECTION_STATUS,
    ),
    _register(
        "ECPConn",
        2,
        1,
        RegisterDataType.BITFIELD16,
        description="Electrical connection point status.",
        bitfield={0x0001: "Connected"},
    ),
    _register(
        "ActWh",
        3,
        4,
        RegisterDataType.ACC64,
        unit="Wh",
        description="AC lifetime active energy output.",
    ),
    _register(
        "ActVAh",
        7,
        4,
        RegisterDataType.ACC64,
        unit="VAh",
        description="AC lifetime apparent energy output; not supported by Fronius.",
    ),
    _register(
        "ActVArhQ1",
        11,
        4,
        RegisterDataType.ACC64,
        unit="varh",
        description=(
            "AC lifetime reactive energy in quadrant 1; not supported by Fronius."
        ),
    ),
    _register(
        "ActVArhQ2",
        15,
        4,
        RegisterDataType.ACC64,
        unit="varh",
        description=(
            "AC lifetime reactive energy in quadrant 2; not supported by Fronius."
        ),
    ),
    _register(
        "ActVArhQ3",
        19,
        4,
        RegisterDataType.ACC64,
        unit="varh",
        description=(
            "AC lifetime reactive energy in quadrant 3; not supported by Fronius."
        ),
    ),
    _register(
        "ActVArhQ4",
        23,
        4,
        RegisterDataType.ACC64,
        unit="varh",
        description=(
            "AC lifetime reactive energy in quadrant 4; not supported by Fronius."
        ),
    ),
    _register(
        "VArAval",
        27,
        1,
        RegisterDataType.INT16,
        unit="var",
        scale_factor="VArAval_SF",
        description="Available reactive power; not supported by Fronius.",
    ),
    _register(
        "VArAval_SF",
        28,
        1,
        RegisterDataType.SUNSSF,
        description="Available reactive-power scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WAval",
        29,
        1,
        RegisterDataType.UINT16,
        unit="W",
        scale_factor="WAval_SF",
        description="Available active power; not supported by Fronius.",
    ),
    _register(
        "WAval_SF",
        30,
        1,
        RegisterDataType.SUNSSF,
        description="Available active-power scale factor; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "StSetLimMsk",
        31,
        2,
        RegisterDataType.BITFIELD32,
        description="Setpoint-limit status; not supported by Fronius.",
        bitfield={
            0x00000001: "WMax",
            0x00000002: "VAMax",
            0x00000004: "VArAval",
            0x00000008: "VArMaxQ1",
            0x00000010: "VArMaxQ2",
            0x00000020: "VArMaxQ3",
            0x00000040: "VArMaxQ4",
            0x00000080: "PFMinQ1",
            0x00000100: "PFMinQ2",
            0x00000200: "PFMinQ3",
            0x00000400: "PFMinQ4",
        },
    ),
    _register(
        "StActCtl",
        33,
        2,
        RegisterDataType.BITFIELD32,
        description="Currently active inverter controls.",
        bitfield={
            0x00000001: "FixedW",
            0x00000002: "FixedVAR",
            0x00000004: "FixedPF",
            0x00000008: "Volt-VAr",
            0x00000010: "Freq-Watt-Param",
            0x00000020: "Freq-Watt-Curve",
            0x00000040: "Dyn-Reactive-Current",
            0x00000080: "LVRT",
            0x00000100: "HVRT",
            0x00000200: "Watt-PF",
            0x00000400: "Volt-Watt",
            0x00001000: "Scheduled",
            0x00002000: "LFRT",
            0x00004000: "HFRT",
        },
    ),
    _register(
        "TmSrc",
        35,
        4,
        RegisterDataType.STRING,
        description="Source of time synchronization.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "Tms",
        39,
        2,
        RegisterDataType.UINT32,
        unit="Secs",
        description="Seconds since 2000-01-01 00:00 UTC.",
    ),
    _register(
        "RtSt",
        41,
        1,
        RegisterDataType.BITFIELD16,
        description="Active ride-through status; not supported by Fronius.",
        bitfield={
            0x0001: "LVRT_ACTIVE",
            0x0002: "HVRT_ACTIVE",
            0x0004: "LFRT_ACTIVE",
            0x0008: "HFRT_ACTIVE",
        },
    ),
    _register(
        "Ris",
        42,
        1,
        RegisterDataType.UINT16,
        unit="ohms",
        scale_factor="Ris_SF",
        description="Isolation resistance.",
    ),
    _register(
        "Ris_SF",
        43,
        1,
        RegisterDataType.SUNSSF,
        description="Isolation-resistance scale factor.",
        poll_class=PollClass.STATIC,
    ),
)

MODEL_122 = SunSpecModelDefinition(
    model_ids=(122,),
    name="Measurements and Status",
    registers=_REGISTERS,
    expected_length=44,
)

_ROLE = PhysicalDeviceRole.INVERTER
MODEL_122 = attach_entities(
    MODEL_122,
    {
        **{
            name: entity(122, name, enabled=False, role=_ROLE)
            for name in (
                "ActWh", "ActVAh", "ActVArhQ1", "ActVArhQ2", "ActVArhQ3",
                "ActVArhQ4", "VArAval", "WAval",
            )
        },
        **{
            name: entity(
                122,
                name,
                category=EntityCategoryHint.DIAGNOSTIC,
                enabled=False,
                role=_ROLE,
            )
            for name in (
                "PVConn", "StorConn", "ECPConn", "StSetLimMsk", "StActCtl",
                "TmSrc", "Tms", "RtSt", "Ris",
            )
        },
    },
)
