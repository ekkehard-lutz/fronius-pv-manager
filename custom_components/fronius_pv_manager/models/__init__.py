"""Home Assistant-independent core data model for Fronius PV Manager."""

from .capability import Capability
from .device import DeviceProfile
from .register import (
    DiscoveredModel,
    EntityDefinition,
    EntityPlatform,
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
    SunSpecModelDefinition,
    ValueRange,
)

__all__ = [
    "Capability",
    "DeviceProfile",
    "DiscoveredModel",
    "EntityDefinition",
    "EntityPlatform",
    "PollClass",
    "RegisterAccess",
    "RegisterDataType",
    "RegisterDefinition",
    "RegisterValue",
    "SunSpecModelDefinition",
    "ValueRange",
]
