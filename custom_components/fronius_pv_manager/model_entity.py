"""Publish register states only when their source model was refreshed."""

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FroniusPVCoordinator


class ModelCoordinatorEntity(CoordinatorEntity[FroniusPVCoordinator]):
    """Retained snapshots must not look like newly reported measurements."""

    @property
    def source_data(self):
        return self.coordinator.data

    def _source_snapshot(self):
        for device in self.source_data.devices:
            if device.device_id == self._source.device_id:
                matching = [
                    model
                    for model in device.decoded_models
                    if model.discovered.model_id == self._source.model_id
                ]
                if self._source.model_occurrence < len(matching):
                    return matching[self._source.model_occurrence]
        return None

    async def async_added_to_hass(self) -> None:
        self._reported_snapshot = self._source_snapshot()
        self._reported_available = self.available
        await super().async_added_to_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        snapshot = self._source_snapshot()
        available = self.available
        if snapshot is not getattr(
            self, "_reported_snapshot", None
        ) or available != getattr(self, "_reported_available", None):
            self._reported_snapshot = snapshot
            self._reported_available = available
            self.async_write_ha_state()
