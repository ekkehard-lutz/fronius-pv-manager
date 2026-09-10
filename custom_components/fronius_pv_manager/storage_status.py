"""Read-only classification of confirmed storage control state."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .storage_entity import StorageEntity, setup_storage_entities


class StorageControlStatus(StorageEntity, SensorEntity):
    """Expose target agreement without identifying or counteracting other writers."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["automatic", "manual_hlc", "low_level_override", "unknown"]

    @property
    def native_value(self) -> str:
        """Unavailable data cannot establish agreement with an HLC target."""
        return self.control.status(self._source.device_id)


def setup_storage_status(entry, async_add_entities):
    """Include status in the sensor platform on initial and later discovery."""
    setup_storage_entities(entry, async_add_entities, _status_entities)


def _status_entities(coordinator, entry_id, source):
    return [StorageControlStatus(coordinator, entry_id, source, "control_status")]
