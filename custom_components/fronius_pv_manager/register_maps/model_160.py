"""SunSpec Model 160 multiple MPPT inverter extension definition.

All offsets are relative to the model payload. They are not Fronius
documentation register numbers or absolute Modbus transport addresses.
"""

from ..models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RepeatingBlockDefinition,
    SunSpecModelDefinition,
)

_READ_ONLY = RegisterAccess.READ_ONLY

_FIXED_REGISTERS = (
    RegisterDefinition(
        name="DCA_SF",
        offset=0,
        size=1,
        data_type=RegisterDataType.SUNSSF,
        access=_READ_ONLY,
        description="DC current scale factor.",
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="DCV_SF",
        offset=1,
        size=1,
        data_type=RegisterDataType.SUNSSF,
        access=_READ_ONLY,
        description="DC voltage scale factor.",
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="DCW_SF",
        offset=2,
        size=1,
        data_type=RegisterDataType.SUNSSF,
        access=_READ_ONLY,
        description="DC power scale factor.",
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="DCWH_SF",
        offset=3,
        size=1,
        data_type=RegisterDataType.SUNSSF,
        access=_READ_ONLY,
        description="DC energy scale factor.",
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="Evt",
        offset=4,
        size=2,
        data_type=RegisterDataType.BITFIELD32,
        access=_READ_ONLY,
        description="Event flags; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    RegisterDefinition(
        name="N",
        offset=6,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        description="Number of modules.",
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="TmsPer",
        offset=7,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        description="Timestamp period; not supported by Fronius.",
        poll_class=PollClass.STATIC,
    ),
)

_MODULE_REGISTERS = (
    RegisterDefinition(
        name="ID",
        offset=0,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="IDStr",
        offset=1,
        size=8,
        data_type=RegisterDataType.STRING,
        access=_READ_ONLY,
        poll_class=PollClass.STATIC,
    ),
    RegisterDefinition(
        name="DCA",
        offset=9,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        unit="A",
        scale_factor="DCA_SF",
        poll_class=PollClass.FAST,
    ),
    RegisterDefinition(
        name="DCV",
        offset=10,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        unit="V",
        scale_factor="DCV_SF",
        poll_class=PollClass.FAST,
    ),
    RegisterDefinition(
        name="DCW",
        offset=11,
        size=1,
        data_type=RegisterDataType.UINT16,
        access=_READ_ONLY,
        unit="W",
        scale_factor="DCW_SF",
        poll_class=PollClass.FAST,
    ),
    RegisterDefinition(
        name="DCWH",
        offset=12,
        size=2,
        data_type=RegisterDataType.ACC32,
        access=_READ_ONLY,
        unit="Wh",
        scale_factor="DCWH_SF",
        poll_class=PollClass.NORMAL,
    ),
    RegisterDefinition(
        name="Tms",
        offset=14,
        size=2,
        data_type=RegisterDataType.UINT32,
        access=_READ_ONLY,
        unit="Secs",
        poll_class=PollClass.NORMAL,
    ),
    RegisterDefinition(
        name="Tmp",
        offset=16,
        size=1,
        data_type=RegisterDataType.INT16,
        access=_READ_ONLY,
        unit="C",
        description="Temperature; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    RegisterDefinition(
        name="DCSt",
        offset=17,
        size=1,
        data_type=RegisterDataType.ENUM16,
        access=_READ_ONLY,
        description="Operating state; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
    RegisterDefinition(
        name="DCEvt",
        offset=18,
        size=2,
        data_type=RegisterDataType.BITFIELD32,
        access=_READ_ONLY,
        description="Event flags; not supported by Fronius.",
        poll_class=PollClass.SLOW,
    ),
)

MODULE_BLOCK = RepeatingBlockDefinition(
    name="module",
    offset=8,
    block_size=20,
    registers=_MODULE_REGISTERS,
)

MODEL_160 = SunSpecModelDefinition(
    model_ids=(160,),
    name="Multiple MPPT Inverter Extension",
    registers=_FIXED_REGISTERS,
    expected_length=88,
    repeating_blocks=(MODULE_BLOCK,),
)
