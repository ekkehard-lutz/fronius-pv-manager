"""Capability-based device metadata."""

from dataclasses import dataclass

from .capability import Capability
from .register import DiscoveredModel


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Describe models and capabilities exposed by one discovered device.

    Product names are intentionally absent: behavior is selected from actual
    models and registers rather than from Fronius marketing names.
    """

    models: tuple[DiscoveredModel, ...] = ()
    capabilities: frozenset[Capability] = frozenset()
