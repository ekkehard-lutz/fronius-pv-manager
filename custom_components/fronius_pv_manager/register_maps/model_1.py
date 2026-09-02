"""Standard SunSpec Common Model 1 register definition.

Offsets are relative to the 65-register model payload and never represent
one-based documentation registers or zero-based transport addresses.
"""

from ..models import (
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
)


def _string_register(
    name: str,
    offset: int,
    size: int,
    description: str,
) -> RegisterDefinition:
    """Create one static read-only Common Model string register."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=size,
        data_type=RegisterDataType.STRING,
        access=RegisterAccess.READ_ONLY,
        description=description,
        poll_class=PollClass.STATIC,
    )


MODEL_1 = SunSpecModelDefinition(
    model_ids=(1,),
    name="Common",
    registers=(
        _string_register("Mn", 0, 16, "Manufacturer."),
        _string_register("Md", 16, 16, "Model."),
        _string_register("Opt", 32, 8, "Options."),
        _string_register("Vr", 40, 8, "Version."),
        _string_register("SN", 48, 16, "Serial number."),
        RegisterDefinition(
            name="DA",
            offset=64,
            size=1,
            data_type=RegisterDataType.UINT16,
            access=RegisterAccess.READ_ONLY,
            description="Device address.",
            poll_class=PollClass.STATIC,
        ),
    ),
    expected_length=65,
)
