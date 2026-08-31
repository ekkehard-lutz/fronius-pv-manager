"""Home Assistant runtime coordinator for SunSpec discovery and polling."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .model_decoder import DecodedModel, decode_model
from .models import DiscoveredModel, SunSpecModelDefinition
from .register_maps import get_model_definition
from .sunspec import SunSpecDiscovery
from .transport import (
    ModbusTcpTransport,
    ModbusTransportError,
    read_holding_registers_chunked,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecodedModelSnapshot:
    """One supported discovered model and its latest decoded payload."""

    discovered: DiscoveredModel
    definition: SunSpecModelDefinition
    decoded: DecodedModel


@dataclass(frozen=True, slots=True)
class FroniusPVCoordinatorData:
    """Immutable topology and decoded model state from one coordinator refresh."""

    discovered_models: tuple[DiscoveredModel, ...]
    decoded_models: tuple[DecodedModelSnapshot, ...]


class FroniusPVCoordinator(DataUpdateCoordinator[FroniusPVCoordinatorData]):
    """Own one persistent Modbus transport and poll its discovered topology."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        transport: ModbusTcpTransport,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.transport = transport
        self.discovered_models: tuple[DiscoveredModel, ...] = ()

    async def async_discover(self) -> None:
        """Connect and discover the SunSpec topology once in the executor."""
        self.discovered_models = await self.hass.async_add_executor_job(
            self._connect_and_discover
        )

    def _connect_and_discover(self) -> tuple[DiscoveredModel, ...]:
        """Perform the complete synchronous initial connection operation."""
        self.transport.connect()
        return SunSpecDiscovery(self.transport).discover()

    async def _async_update_data(self) -> FroniusPVCoordinatorData:
        """Poll and decode all supported models without blocking the event loop."""
        try:
            return await self.hass.async_add_executor_job(self._poll_models)
        except ModbusTransportError as err:
            raise UpdateFailed("failed to read SunSpec model data") from err

    def _poll_models(self) -> FroniusPVCoordinatorData:
        """Read one complete synchronous snapshot using cached discovery results."""
        decoded_models = []
        for discovered in self.discovered_models:
            definition = get_model_definition(discovered.model_id)
            if definition is None:
                continue
            payload = read_holding_registers_chunked(
                self.transport,
                discovered.base_address,
                discovered.length,
            )
            decoded_models.append(
                DecodedModelSnapshot(
                    discovered=discovered,
                    definition=definition,
                    decoded=decode_model(definition, payload),
                )
            )
        return FroniusPVCoordinatorData(
            discovered_models=self.discovered_models,
            decoded_models=tuple(decoded_models),
        )

    async def async_close(self) -> None:
        """Close the persistent synchronous transport in the executor."""
        await self.hass.async_add_executor_job(self.transport.close)
