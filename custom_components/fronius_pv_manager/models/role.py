"""Physical device roles independent of entity presentation metadata."""

from enum import StrEnum


class PhysicalDeviceRole(StrEnum):
    """Physical device that should own a future Home Assistant entity."""

    INVERTER = "inverter"
    STORAGE = "storage"
    METER = "meter"
