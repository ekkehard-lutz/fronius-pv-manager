"""Home Assistant runtime coordinator for SunSpec discovery and polling."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType

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
class DeviceSnapshot:
    """Immutable topology and decoded state for one Modbus device ID."""

    device_id: int
    discovered_models: tuple[DiscoveredModel, ...]
    decoded_models: tuple[DecodedModelSnapshot, ...]
    available: bool = True


@dataclass(frozen=True, slots=True)
class FroniusPVCoordinatorData:
    """Immutable state for every configured Modbus device ID."""

    devices: tuple[DeviceSnapshot, ...]

    @property
    def discovered_models(self) -> tuple[DiscoveredModel, ...]:
        """Return all discovered models for compatibility and diagnostics."""
        return tuple(
            model for device in self.devices for model in device.discovered_models
        )

    @property
    def decoded_models(self) -> tuple[DecodedModelSnapshot, ...]:
        """Return all decoded models while preserving per-device ordering."""
        return tuple(
            model for device in self.devices for model in device.decoded_models
        )


class FroniusPVCoordinator(DataUpdateCoordinator[FroniusPVCoordinatorData]):
    """Own persistent per-device transports and poll discovered topologies."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        transports: Mapping[int, ModbusTcpTransport],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        if not transports:
            raise ValueError("at least one Modbus device transport is required")
        self.transports = MappingProxyType(dict(transports))
        self.discovered_models_by_device: dict[
            int, tuple[DiscoveredModel, ...]
        ] = {}

    @property
    def discovered_models(self) -> tuple[DiscoveredModel, ...]:
        """Return the flattened discovered topology for compatibility."""
        return tuple(
            model
            for device_id in self.transports
            for model in self.discovered_models_by_device.get(device_id, ())
        )

    async def async_discover(self) -> None:
        """Connect and discover the SunSpec topology once in the executor."""
        self.discovered_models_by_device = await self.hass.async_add_executor_job(
            self._connect_and_discover
        )

    def _connect_and_discover(self) -> dict[int, tuple[DiscoveredModel, ...]]:
        """Connect and discover every configured device synchronously."""
        discovered = {}
        for device_id, transport in self.transports.items():
            transport.connect()
            discovered[device_id] = SunSpecDiscovery(transport).discover()
        return discovered

    async def _async_update_data(self) -> FroniusPVCoordinatorData:
        """Poll and decode all supported models without blocking the event loop."""
        try:
            return await self.hass.async_add_executor_job(self._poll_devices)
        except ModbusTransportError as err:
            raise UpdateFailed("failed to read SunSpec device data") from err

    def _poll_devices(self) -> FroniusPVCoordinatorData:
        """Poll every device without retaining stale data after partial failure."""
        devices = []
        for device_id, transport in self.transports.items():
            discovered = self.discovered_models_by_device[device_id]
            try:
                decoded = self._poll_device(transport, discovered)
            except ModbusTransportError:
                _LOGGER.warning(
                    "Failed to poll Modbus device ID %s", device_id, exc_info=True
                )
                devices.append(
                    DeviceSnapshot(device_id, discovered, (), available=False)
                )
            else:
                devices.append(DeviceSnapshot(device_id, discovered, decoded))
        if not any(device.available for device in devices):
            raise ModbusTransportError("all configured Modbus devices failed to poll")
        return FroniusPVCoordinatorData(tuple(devices))

    @staticmethod
    def _poll_device(
        transport: ModbusTcpTransport,
        discovered_models: tuple[DiscoveredModel, ...],
    ) -> tuple[DecodedModelSnapshot, ...]:
        """Decode all supported models for one device context."""
        decoded_models = []
        for discovered in discovered_models:
            definition = get_model_definition(discovered.model_id)
            if definition is None:
                continue
            payload = read_holding_registers_chunked(
                transport,
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
        return tuple(decoded_models)

    async def async_close(self) -> None:
        """Close every owned synchronous transport in the executor."""
        await self.hass.async_add_executor_job(self._close_transports)

    def _close_transports(self) -> None:
        """Attempt every close and raise the first transport error afterward."""
        first_error = None
        for transport in self.transports.values():
            try:
                transport.close()
            except ModbusTransportError as err:
                if first_error is None:
                    first_error = err
        if first_error is not None:
            raise first_error
