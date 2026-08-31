"""Home Assistant-independent core data model for Fronius PV Manager."""

from .capability import Capability
from .device import DeviceProfile
from .register import (
    DiscoveredModel,
    DiscoveredRepeatingBlockInstance,
    EntityCategoryHint,
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
    get_help_text,
)
from .role import PhysicalDeviceRole

__all__ = [
    "Capability",
    "DeviceProfile",
    "DiscoveredModel",
    "DiscoveredRepeatingBlockInstance",
    "EntityCategoryHint",
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
    "get_help_text",
]
