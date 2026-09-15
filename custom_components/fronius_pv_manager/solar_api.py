"""Optional, read-only local Solar API semantic state supplement."""

import logging
from dataclasses import dataclass, field

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SolarState:
    """Validated fields; missing/invalid fields never invalidate siblings."""

    backup_mode: bool | None = None
    battery_standby: bool | None = None
    battery_modes: dict[str, str] = field(default_factory=dict)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def parse_solar_state(payload) -> SolarState:
    """Read exactly three semantic fields without coercion or normalization."""
    data = _mapping(_mapping(_mapping(payload).get("Body")).get("Data"))
    site = _mapping(data.get("Site"))
    modes = {}
    for device_id, inverter in _mapping(data.get("Inverters")).items():
        mode = _mapping(inverter).get("Battery_Mode")
        # Whitespace-only strings carry no state; preserve all other strings exactly.
        if isinstance(mode, str) and mode.strip():
            modes[str(device_id)] = mode
    return SolarState(
        backup_mode=(
            site.get("BackupMode") if type(site.get("BackupMode")) is bool else None
        ),
        battery_standby=(
            site.get("BatteryStandby")
            if type(site.get("BatteryStandby")) is bool
            else None
        ),
        battery_modes=modes,
    )


class SolarAPI:
    """Reuse HA's session with bounded requests and no redirects off endpoint."""

    def __init__(self, hass, host):
        self.hass = hass
        self.url = URL.build(scheme="http", host=host).with_path(
            "/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        )
        self.available = False

    async def async_poll(self) -> SolarState:
        """Isolate optional HTTP failures and retry on the next runtime poll."""
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                self.url,
                timeout=aiohttp.ClientTimeout(total=3),
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise ValueError(f"HTTP {response.status}")
                payload = await response.json()
            state = parse_solar_state(payload)
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as err:
            if self.available:
                _LOGGER.debug("Local Solar API unavailable: %s", err)
            self.available = False
            return SolarState()
        if not self.available:
            _LOGGER.debug("Local Solar API reachable")
        self.available = True
        return state
