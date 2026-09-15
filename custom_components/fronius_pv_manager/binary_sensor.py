"""Read-only High-Level Solar API boolean states."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .solar_entity import SolarEntity, setup_solar_entities


class SolarBinarySensor(SolarEntity, BinarySensorEntity):
    """Expose a strict JSON boolean without inferring Modbus semantics."""

    @property
    def is_on(self):
        return self.value


async def async_setup_entry(hass, entry, async_add_entities):
    """Attach supplemental states to the existing logical devices."""
    setup_solar_entities(entry, async_add_entities, _entities)


def _entities(coordinator, entry_id, device_id):
    return [
        SolarBinarySensor(coordinator, entry_id, device_id, "backup_mode", "inverter"),
        SolarBinarySensor(
            coordinator, entry_id, device_id, "battery_standby", "storage"
        ),
    ]
