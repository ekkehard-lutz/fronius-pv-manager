"""High-level grid-charging permission control."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import ServiceValidationError

from .storage_entity import StorageEntity, setup_storage_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_storage_entities(entry, async_add_entities, _storage_switches)


class GridChargingSwitch(StorageEntity, SwitchEntity):
    """Grid permission; Fronius internal/service charging remains independent."""

    @property
    def is_on(self):
        try:
            raw = self.control.snapshot(self._source.device_id)["ChaGriSet"].raw
            return bool(raw) if raw in (0, 1) else None
        except ServiceValidationError:
            return None

    async def async_turn_on(self, **kwargs):
        await self.write_register("ChaGriSet", 1)

    async def async_turn_off(self, **kwargs):
        await self.write_register("ChaGriSet", 0)


def _storage_switches(coordinator, entry_id, source):
    return [GridChargingSwitch(coordinator, entry_id, source, "grid_charging_allowed")]
