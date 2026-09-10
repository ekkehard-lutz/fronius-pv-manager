"""Temporary development-only hardware harness; remove before stable v0.3.0."""

import math

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .storage_control import PowerSettings

TEST_OWNER = f"{DOMAIN}.debug_test"
_DATA_KEY = f"{DOMAIN}.debug_services"
POWER_FIELDS = tuple(PowerSettings.__dataclass_fields__)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise vol.Invalid("a finite number is required")
    return value


def _watts(value):
    value = _number(value)
    if value < 0 or value != int(value):
        raise vol.Invalid("nonnegative whole watts are required")
    return int(value)


def _device_id(value):
    if type(value) is not int or not 1 <= value <= 247:
        raise vol.Invalid("device_id must be a Modbus unit ID from 1 to 247")
    return value


def _complete(data):
    present = set(data).intersection(POWER_FIELDS)
    if present and present != set(POWER_FIELDS):
        raise vol.Invalid("provide all four power settings or omit all four")
    return data


_BASE = {vol.Required("device_id"): _device_id, vol.Optional("config_entry_id"): str}
SCHEMAS = {
    "debug_remote_acquire": vol.All(
        vol.Schema({**_BASE, **{vol.Optional(key): _watts for key in POWER_FIELDS}}),
        _complete,
    ),
    "debug_remote_set_window": vol.Schema(
        {**_BASE, **{vol.Required(key): _watts for key in POWER_FIELDS}}
    ),
    "debug_remote_heartbeat": vol.Schema(_BASE),
    "debug_remote_release": vol.Schema(_BASE),
    "debug_remote_set_minimum_reserve": vol.Schema(
        {**_BASE, vol.Required("value"): vol.All(_number, vol.Range(min=5, max=100))}
    ),
    "debug_remote_set_grid_charging_allowed": vol.Schema(
        {**_BASE, vol.Required("enabled"): bool}
    ),
}


def async_register_debug_services(hass, entry):
    """Register once and retain only successfully loaded endpoint runtimes."""
    entries = hass.data.setdefault(_DATA_KEY, {})
    entries[entry.entry_id] = entry.runtime_data
    if len(entries) > 1:
        return

    async def handle(call):
        device_id = call.data["device_id"]
        entry_id = call.data.get("config_entry_id")
        candidates = [
            coordinator
            for key, coordinator in entries.items()
            if (entry_id is None or key == entry_id)
            and device_id in coordinator.transports
        ]
        if len(candidates) != 1:
            raise ServiceValidationError(
                "device_id is unavailable or ambiguous; "
                "specify a loaded config_entry_id"
            )
        control = candidates[0].storage_control
        args = (device_id, TEST_OWNER)
        if call.service in ("debug_remote_acquire", "debug_remote_set_window"):
            settings = (
                PowerSettings(**{key: call.data[key] for key in POWER_FIELDS})
                if POWER_FIELDS[0] in call.data
                else None
            )
            if call.service == "debug_remote_acquire":
                await control.async_acquire_remote_control(*args, settings)
            else:
                await control.async_set_remote_power_window(*args, settings)
        elif call.service == "debug_remote_heartbeat":
            await control.async_remote_heartbeat(*args)
        elif call.service == "debug_remote_release":
            await control.async_release_remote_control(*args)
        elif call.service == "debug_remote_set_minimum_reserve":
            await control.async_set_remote_minimum_reserve(*args, call.data["value"])
        else:
            await control.async_set_remote_grid_charging_allowed(
                *args, call.data["enabled"]
            )

    for name, schema in SCHEMAS.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handle, schema=schema)


def async_unregister_debug_services(hass, entry):
    """Remove the harness when its last loaded endpoint is unloaded."""
    entries = hass.data.get(_DATA_KEY)
    if entries is None:
        return
    entries.pop(entry.entry_id, None)
    if not entries:
        for name in SCHEMAS:
            hass.services.async_remove(DOMAIN, name)
        hass.data.pop(_DATA_KEY)
