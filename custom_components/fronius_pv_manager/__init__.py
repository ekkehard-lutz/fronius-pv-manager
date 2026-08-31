"""Home Assistant config-entry lifecycle for Fronius PV Manager."""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_PORT, DEFAULT_PORT, DEFAULT_UNIT_ID
from .coordinator import FroniusPVCoordinator
from .sunspec import SunSpecDiscoveryError
from .transport import ModbusTcpTransport, ModbusTransportError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = (Platform.SENSOR,)

type FroniusPVConfigEntry = ConfigEntry[FroniusPVCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: FroniusPVConfigEntry
) -> bool:
    """Connect, discover once, and perform the first coordinator refresh."""
    transport = ModbusTcpTransport(
        entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        device_id=entry.data.get(CONF_DEVICE_ID, DEFAULT_UNIT_ID),
    )
    coordinator = FroniusPVCoordinator(hass, entry, transport)
    try:
        await coordinator.async_discover()
        await coordinator.async_config_entry_first_refresh()
    except (ModbusTransportError, SunSpecDiscoveryError, ConfigEntryNotReady) as err:
        try:
            await coordinator.async_close()
        except ModbusTransportError:
            _LOGGER.debug(
                "Failed to close transport after setup failure", exc_info=True
            )
        if isinstance(err, ConfigEntryNotReady):
            raise
        raise ConfigEntryNotReady("Fronius SunSpec device is unavailable") from err
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        try:
            await coordinator.async_close()
        except ModbusTransportError:
            _LOGGER.warning(
                "Failed to close transport after platform setup failure",
                exc_info=True,
            )
        del entry.runtime_data
        raise
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FroniusPVConfigEntry
) -> bool:
    """Stop coordinator activity and close its persistent transport safely."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    coordinator = entry.runtime_data
    await coordinator.async_shutdown()
    try:
        await coordinator.async_close()
    except ModbusTransportError:
        _LOGGER.warning("Failed to close Fronius Modbus transport", exc_info=True)
    if hasattr(entry, "runtime_data"):
        del entry.runtime_data
    return True
