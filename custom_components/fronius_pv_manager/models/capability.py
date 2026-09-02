"""Capabilities derived from discovered SunSpec models and registers."""

from enum import StrEnum


class Capability(StrEnum):
    """Features a device can expose independently of its product name."""

    INVERTER = "inverter"
    STORAGE = "storage"
    METER = "meter"
    MPPT = "mppt"
    CONTROLS = "controls"
    STORAGE_CONTROL = "storage_control"
    GRID_CHARGE_CONTROL = "grid_charge_control"
