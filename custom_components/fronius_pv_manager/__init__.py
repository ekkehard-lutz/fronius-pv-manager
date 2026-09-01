"""Home Assistant config-entry lifecycle for Fronius PV Manager."""

import logging
from pathlib import Path
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
)
from .coordinator import FroniusPVCoordinator
from .sunspec import SunSpecDiscoveryError
from .transport import ModbusTcpTransport, ModbusTransportError
from .write_policy_loader import WritePolicyLoadError, load_or_create_write_policy

_LOGGER = logging.getLogger(__name__)
PLATFORMS = (Platform.SENSOR, Platform.NUMBER, Platform.SELECT)

type FroniusPVConfigEntry = ConfigEntry[FroniusPVCoordinator]


def _configured_device_ids(entry: FroniusPVConfigEntry) -> tuple[int, ...]:
    """Normalize new multi-device data with the development fallback key."""
    configured = entry.data.get(CONF_DEVICE_IDS)
    if configured is None:
        return (entry.data.get(CONF_DEVICE_ID, DEFAULT_UNIT_ID),)
    device_ids = tuple(configured)
    if not device_ids:
        raise ValueError("at least one Modbus device ID must be configured")
    if len(set(device_ids)) != len(device_ids):
        raise ValueError("Modbus device IDs must be unique")
    return device_ids


async def async_setup_entry(
    hass: HomeAssistant, entry: FroniusPVConfigEntry
) -> bool:
    """Connect, discover once, and perform the first coordinator refresh."""
    try:
        policy_path, write_policies = await hass.async_add_executor_job(
            load_or_create_write_policy,
            Path(hass.config.path()),
        )
    except (OSError, WritePolicyLoadError) as err:
        policy_path = Path(hass.config.path("fronius_pv_manager", "write_policy.yaml"))
        write_policies = MappingProxyType({})
        _LOGGER.error(
            "Write policy %s is invalid; writes are disabled: %s",
            policy_path,
            err,
        )
    transports = {
        device_id: ModbusTcpTransport(
            entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            device_id=device_id,
        )
        for device_id in _configured_device_ids(entry)
    }
    coordinator = FroniusPVCoordinator(hass, entry, transports, write_policies)
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
