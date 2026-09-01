"""Home Assistant Entity Registry tests for stable semantic object IDs."""

from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.fronius_pv_manager.const import DOMAIN


class _ConfigEntries:
    """Provide the config-entry identity required by Device Registry."""

    def async_get_entry(self, entry_id: str):
        """Return one minimal existing integration config entry."""
        if entry_id != "test-entry":
            return None
        return SimpleNamespace(
            entry_id=entry_id,
            domain=DOMAIN,
            subentries={},
            disabled_by=None,
        )


@pytest.mark.asyncio
async def test_semantic_object_id_registration_and_unique_id_reuse(tmp_path) -> None:
    """Core registry prefixes the device and reuses an unchanged unique ID."""
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = _ConfigEntries()
    device_registry = dr.DeviceRegistry(hass)
    hass.data[dr.DATA_REGISTRY] = device_registry
    await device_registry.async_load()
    entity_registry = er.async_get(hass)
    await entity_registry.async_load()

    try:
        device = device_registry.async_get_or_create(
            config_entry_id="test-entry",
            identifiers={(DOMAIN, "test-entry:device1:storage")},
            name="Speicher",
        )
        first = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            "stable-unique-id",
            device_id=device.id,
            has_entity_name=True,
            object_id_base="discharging_current",
        )
        second = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            "stable-unique-id",
            device_id=device.id,
            has_entity_name=True,
            object_id_base="a_changed_suggestion",
        )

        assert first.entity_id == "sensor.speicher_discharging_current"
        assert second.id == first.id
        assert second.entity_id == first.entity_id
        assert len(entity_registry.entities) == 1
    finally:
        await hass.async_stop(force=True)
