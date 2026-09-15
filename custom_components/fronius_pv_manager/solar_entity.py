"""High-level Solar API entities on existing inverter/storage devices."""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class SolarEntity(CoordinatorEntity):
    """Availability depends only on the individual Solar API field."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id, device_id, key, role):
        super().__init__(coordinator)
        self.device_id = device_id
        self.key = key
        self.role = role
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_device{device_id}_{role}_hlc_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}:device{device_id}:{role}")},
            translation_key=f"{role}_device",
        )

    @property
    def suggested_object_id(self):
        return f"{self.role}_{self.key}"

    @property
    def value(self):
        state = self.coordinator.solar_state
        if self.key == "battery_operation_mode":
            return state.battery_modes.get(str(self.device_id))
        return getattr(state, self.key)

    @property
    def available(self):
        return self.value is not None


def setup_solar_entities(entry, async_add_entities, factory):
    """Use cached/discovered topology and add entities when devices appear."""
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_new():
        fresh = []
        for device in coordinator.entity_data.devices:
            models = {model.model_id for model in device.discovered_models}
            if not models.intersection({101, 102, 103, 111, 112, 113, 124}):
                continue
            for entity in factory(coordinator, entry.entry_id, device.device_id):
                if entity.unique_id not in known:
                    known.add(entity.unique_id)
                    fresh.append(entity)
        if fresh:
            async_add_entities(fresh)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
