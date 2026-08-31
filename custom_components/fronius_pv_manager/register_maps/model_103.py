"""SunSpec Model 103 three-phase inverter integer-plus-SF definition.

Offsets are relative to the 50-register model payload and never represent
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
from ._entity_catalog import attach_entities, attach_help_texts, entity

_INVERTER_STATES = {
    1: "Off",
    2: "Sleeping",
    3: "Starting",
    4: "MPPT",
    5: "Throttled",
    6: "Shutting down",
    7: "Fault",
    8: "Standby",
}

_EVENT_SEVERITIES = {
    0x00000001: "Error",
    0x00000002: "Warning",
    0x00000004: "Info",
}


def _register(
    name: str,
    offset: int,
    data_type: RegisterDataType,
    *,
    size: int = 1,
    unit: str | None = None,
    scale_factor: str | None = None,
    description: str | None = None,
    enum: Mapping[int, str] | None = None,
    bitfield: Mapping[int, str] | None = None,
    poll_class: PollClass = PollClass.FAST,
) -> RegisterDefinition:
    """Create one read-only Model 103 register definition."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        unit=unit,
        scale_factor=scale_factor,
        description=description,
        enum=enum,
        bitfield=bitfield,
        poll_class=poll_class,
    )


_REGISTERS = (
    _register("A", 0, RegisterDataType.UINT16, unit="A", scale_factor="A_SF"),
    _register("AphA", 1, RegisterDataType.UINT16, unit="A", scale_factor="A_SF"),
    _register("AphB", 2, RegisterDataType.UINT16, unit="A", scale_factor="A_SF"),
    _register("AphC", 3, RegisterDataType.UINT16, unit="A", scale_factor="A_SF"),
    _register(
        "A_SF",
        4,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "PPVphAB", 5, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "PPVphBC", 6, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "PPVphCA", 7, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "PhVphA", 8, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "PhVphB", 9, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "PhVphC", 10, RegisterDataType.UINT16, unit="V", scale_factor="V_SF"
    ),
    _register(
        "V_SF",
        11,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register("W", 12, RegisterDataType.INT16, unit="W", scale_factor="W_SF"),
    _register(
        "W_SF",
        13,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "Hz", 14, RegisterDataType.UINT16, unit="Hz", scale_factor="Hz_SF"
    ),
    _register(
        "Hz_SF",
        15,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register("VA", 16, RegisterDataType.INT16, unit="VA", scale_factor="VA_SF"),
    _register(
        "VA_SF",
        17,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "VAr", 18, RegisterDataType.INT16, unit="var", scale_factor="VAr_SF"
    ),
    _register(
        "VAr_SF",
        19,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register("PF", 20, RegisterDataType.INT16, unit="Pct", scale_factor="PF_SF"),
    _register(
        "PF_SF",
        21,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "WH",
        22,
        RegisterDataType.ACC32,
        size=2,
        unit="Wh",
        scale_factor="WH_SF",
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "WH_SF",
        24,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "DCA",
        25,
        RegisterDataType.UINT16,
        unit="A",
        scale_factor="DCA_SF",
        description="Not supported for inverters with multiple DC inputs.",
    ),
    _register(
        "DCA_SF",
        26,
        RegisterDataType.SUNSSF,
        description="Not supported by Fronius for this device layout.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "DCV",
        27,
        RegisterDataType.UINT16,
        unit="V",
        scale_factor="DCV_SF",
        description="Not supported for inverters with multiple DC inputs.",
    ),
    _register(
        "DCV_SF",
        28,
        RegisterDataType.SUNSSF,
        description="Not supported by Fronius for this device layout.",
        poll_class=PollClass.STATIC,
    ),
    _register(
        "DCW", 29, RegisterDataType.INT16, unit="W", scale_factor="DCW_SF"
    ),
    _register(
        "DCW_SF",
        30,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "TmpCab",
        31,
        RegisterDataType.INT16,
        unit="C",
        scale_factor="Tmp_SF",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "TmpSnk",
        32,
        RegisterDataType.INT16,
        unit="C",
        scale_factor="Tmp_SF",
        description="Not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "TmpTrns",
        33,
        RegisterDataType.INT16,
        unit="C",
        scale_factor="Tmp_SF",
        description="Not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "TmpOt",
        34,
        RegisterDataType.INT16,
        unit="C",
        scale_factor="Tmp_SF",
        description="Not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "Tmp_SF",
        35,
        RegisterDataType.SUNSSF,
        poll_class=PollClass.STATIC,
    ),
    _register(
        "St",
        36,
        RegisterDataType.ENUM16,
        enum=_INVERTER_STATES,
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "StVnd",
        37,
        RegisterDataType.ENUM16,
        description="Vendor operating state using the same codes as St.",
        enum=_INVERTER_STATES,
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "Evt1",
        38,
        RegisterDataType.BITFIELD32,
        size=2,
        description="SunSpec event flags.",
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "Evt2",
        40,
        RegisterDataType.BITFIELD32,
        size=2,
        description="Reserved for future use.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "EvtVnd1",
        42,
        RegisterDataType.BITFIELD32,
        size=2,
        description="Customer-view event severity flags.",
        bitfield=_EVENT_SEVERITIES,
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "EvtVnd2",
        44,
        RegisterDataType.BITFIELD32,
        size=2,
        description="Technician-view event severity flags.",
        bitfield=_EVENT_SEVERITIES,
        poll_class=PollClass.NORMAL,
    ),
    _register(
        "EvtVnd3",
        46,
        RegisterDataType.BITFIELD32,
        size=2,
        description="Vendor event flags; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    _register(
        "EvtVnd4",
        48,
        RegisterDataType.BITFIELD32,
        size=2,
        description="Vendor event flags; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
)

MODEL_103 = SunSpecModelDefinition(
    model_ids=(103,),
    name="Three-Phase Inverter Integer + Scale Factors",
    registers=_REGISTERS,
    expected_length=50,
)

_ROLE = PhysicalDeviceRole.INVERTER
MODEL_103 = attach_entities(
    MODEL_103,
    {
        **{
            name: entity(
                103,
                name,
                enabled=name
                in {
                    "AphA",
                    "AphB",
                    "AphC",
                    "PPVphAB",
                    "PPVphBC",
                    "PPVphCA",
                    "PhVphA",
                    "PhVphB",
                    "PhVphC",
                    "W",
                    "Hz",
                    "WH",
                    "DCW",
                    "St",
                },
                role=_ROLE,
                translate_enum_values=name == "St",
            )
            for name in (
                "A", "AphA", "AphB", "AphC", "PPVphAB", "PPVphBC", "PPVphCA",
                "PhVphA", "PhVphB", "PhVphC", "W", "Hz", "VA", "VAr", "PF",
                "WH", "DCA", "DCV", "DCW", "St",
            )
        },
        **{
            name: entity(
                103,
                name,
                category=EntityCategoryHint.DIAGNOSTIC,
                enabled=False,
                role=_ROLE,
            )
            for name in (
                "TmpCab", "TmpSnk", "TmpTrns", "TmpOt", "StVnd", "Evt1", "Evt2",
                "EvtVnd1", "EvtVnd2", "EvtVnd3", "EvtVnd4",
            )
        },
    },
)
MODEL_103 = attach_help_texts(
    MODEL_103,
    {
        "W": {
            "en": "Current AC active power reported by the inverter.",
            "de": "Aktuelle vom Wechselrichter gemeldete AC-Wirkleistung.",
        },
        "WH": {
            "en": "Accumulated lifetime AC energy reported by the inverter.",
            "de": "Vom Wechselrichter gemeldete, insgesamt erzeugte AC-Energie.",
        },
    },
)
