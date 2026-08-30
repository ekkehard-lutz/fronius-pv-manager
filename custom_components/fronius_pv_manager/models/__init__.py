"""Home Assistant-independent core data model for Fronius PV Manager."""

from .capability import Capability
from .device import DeviceProfile
from .register import (
    DiscoveredModel,
    DiscoveredRepeatingBlockInstance,
    EntityDefinition,
    EntityPlatform,
    PollClass,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
    RepeatingBlockDefinition,
    SunSpecModelDefinition,
    ValueRange,
)
from .role import PhysicalDeviceRole

__all__ = [
    "Capability",
    "DeviceProfile",
    "DiscoveredModel",
    "DiscoveredRepeatingBlockInstance",
    "EntityDefinition",
    "EntityPlatform",
    "PollClass",
    "PhysicalDeviceRole",
    "RegisterAccess",
    "RegisterDataType",
    "RegisterDefinition",
    "RegisterValue",
    "RepeatingBlockDefinition",
    "SunSpecModelDefinition",
    "ValueRange",
]
