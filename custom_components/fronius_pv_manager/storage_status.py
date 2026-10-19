"""Read-only classification of confirmed storage control state."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback

from .storage_entity import StorageEntity, setup_storage_entities


class StorageControlStatus(StorageEntity, SensorEntity):
    """Expose target agreement without identifying or counteracting other writers."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["automatic", "manual_hlc", "low_level_override", "unknown"]

    async def async_added_to_hass(self):
        self._capture_status()
        self._reported_status = (self.available, self.native_value)
        await super().async_added_to_hass()

    def _capture_status(self):
        epoch = self.coordinator.measurement_epochs.get(self._source.device_id, 0)
        if epoch != getattr(self, "_status_epoch", epoch):
            self._held_status = None
        self._status_epoch = epoch
        if super().available:
            self._held_status = self.control.status(self._source.device_id)
        elif not hasattr(self, "_held_status"):
            self._held_status = None

    @callback
    def _handle_coordinator_update(self):
        self._capture_status()
        state = (self.available, self.native_value)
        if state != getattr(self, "_reported_status", None):
            self._reported_status = state
            self.async_write_ha_state()

    @property
    def available(self):
        if hasattr(self, "_held_status"):
            return self._held_status is not None
        return super().available

    @property
    def native_value(self) -> str:
        """Display last confirmed classification, never granting write authority."""
        if hasattr(self, "_held_status"):
            return self._held_status or "unknown"
        return self.control.status(self._source.device_id)


def setup_storage_status(entry, async_add_entities):
    """Include status in the sensor platform on initial and later discovery."""
    setup_storage_entities(entry, async_add_entities, _status_entities)


def _status_entities(coordinator, entry_id, source):
    return [StorageControlStatus(coordinator, entry_id, source, "control_status")]
