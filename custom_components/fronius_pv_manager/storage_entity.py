"""Shared discovery, identity and validation for user-facing storage controls."""

from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .control_entity import control_entity_sources
from .models import EntityPlatform
from .sensor import _device_info
from .write_runtime import WriteInvalidValueError


class StorageEntity(CoordinatorEntity):
    """One stable high-level entity associated with the physical storage device."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, entry_id, source, key):
        super().__init__(coordinator)
        self._source = source
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_device{source.device_id}_storage_hlc_{key}"
        self._attr_device_info = _device_info(entry_id, source, False)

    @property
    def suggested_object_id(self):
        """Keep object IDs independent of the selected display language."""
        return f"storage_{self.key}"

    @property
    def control(self):
        return self.coordinator.storage_control

    @property
    def available(self):
        if not super().available:
            return False
        try:
            self.control.snapshot(self._source.device_id)
            if self.key.endswith("power"):
                self.control.power_step(self._source.device_id)
        except ServiceValidationError:
            return False
        return True

    async def write_register(self, name, value):
        try:
            await self.coordinator.write_runtime.async_write(
                self._source.device_id, 124, name, value
            )
        except WriteInvalidValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def change(self, **kwargs):
        try:
            await self.control.async_change(self._source.device_id, **kwargs)
        except WriteInvalidValueError as err:
            raise ServiceValidationError(str(err)) from err


def setup_storage_entities(entry, async_add_entities, factory):
    """Add controls on initial or later discovery, independent of write policy."""
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_new():
        entities = []
        for source in control_entity_sources(coordinator, EntityPlatform.NUMBER):
            if source.model_id != 124 or source.register_name != "MinRsvPct":
                continue
            # The existing write runtime addresses the first model only.
            if source.model_occurrence != 0:
                continue
            for entity in factory(coordinator, entry.entry_id, source):
                if entity.unique_id not in known:
                    known.add(entity.unique_id)
                    entities.append(entity)
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
