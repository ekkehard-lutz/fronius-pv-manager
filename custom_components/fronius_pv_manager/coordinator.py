"""Home Assistant runtime coordinator for SunSpec discovery and polling."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .model_decoder import DecodedModel, decode_model
from .models import DiscoveredModel, SunSpecModelDefinition
from .register_maps import get_model_definition
from .storage_control import StorageControl
from .sunspec import SunSpecDiscovery, SunSpecDiscoveryError
from .topology import CONF_TOPOLOGY, model_topology, restore_model
from .transport import (
    ModbusDeviceTransport,
    ModbusTransportError,
    read_holding_registers_chunked,
)
from .write_policy import WritePolicy
from .write_runtime import FroniusPVWriteRuntime

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecodedModelSnapshot:
    """One supported discovered model and its latest decoded payload."""

    discovered: DiscoveredModel
    definition: SunSpecModelDefinition
    decoded: DecodedModel
    available: bool = True


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
    """Own device-bound views and poll their discovered topologies."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        transports: Mapping[int, ModbusDeviceTransport],
        write_policies: Mapping[tuple[int, str], WritePolicy] | None = None,
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
        self.discovered_models_by_device: dict[int, tuple[DiscoveredModel, ...]] = {}
        self.entry = entry
        self.topology = dict(entry.data.get(CONF_TOPOLOGY, {}))
        for device_id in transports:
            records = self.topology.get(str(device_id), ())
            if records:
                self.discovered_models_by_device[device_id] = tuple(
                    restore_model(record)[0] for record in records
                )
        self.data = FroniusPVCoordinatorData(
            tuple(
                DeviceSnapshot(
                    device_id,
                    self.discovered_models_by_device.get(device_id, ()),
                    (),
                    available=False,
                )
                for device_id in transports
            )
        )
        self.write_policies = MappingProxyType(dict(write_policies or {}))
        # This serializes this config entry only; external Modbus clients remain
        # independent TCP sessions outside Home Assistant's control.
        self._io_lock = asyncio.Lock()
        self.write_runtime = FroniusPVWriteRuntime(self)
        self.storage_control = StorageControl(self)

    @property
    def io_lock(self) -> asyncio.Lock:
        """Return the config-entry lock shared by polling and writes."""
        return self._io_lock

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
        async with self._io_lock:
            self.discovered_models_by_device = await self.hass.async_add_executor_job(
                self._connect_and_discover
            )

    def _connect_and_discover(self) -> dict[int, tuple[DiscoveredModel, ...]]:
        """Connect and discover every configured device synchronously."""
        discovered = {}
        connected: set[int] = set()
        for device_id, transport in self.transports.items():
            owner = getattr(transport, "endpoint", transport)
            if id(owner) not in connected:
                owner.connect()
                connected.add(id(owner))
            discovered[device_id] = SunSpecDiscovery(transport).discover()
        return discovered

    async def _async_update_data(self) -> FroniusPVCoordinatorData:
        """Poll and decode all supported models without blocking the event loop."""
        async with self._io_lock:
            data = await self.hass.async_add_executor_job(self._poll_devices)
        if self.entry.data.get(CONF_TOPOLOGY) != self.topology:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_TOPOLOGY: self.topology}
            )
        return data

    @property
    def entity_data(self) -> FroniusPVCoordinatorData:
        """Expose cached structure separately from current measurements."""
        devices = []
        for device_id in self.transports:
            if str(device_id) not in self.topology:
                current = next(
                    (
                        device
                        for device in self.data.devices
                        if device.device_id == device_id
                    ),
                    None,
                )
                if current is not None:
                    devices.append(current)
                    continue
            snapshots = []
            discovered_models = []
            for record in self.topology.get(str(device_id), ()):
                discovered, decoded = restore_model(record)
                discovered_models.append(discovered)
                definition = get_model_definition(discovered.model_id)
                if definition is not None:
                    snapshots.append(
                        DecodedModelSnapshot(discovered, definition, decoded)
                    )
            devices.append(
                DeviceSnapshot(
                    device_id,
                    tuple(discovered_models),
                    tuple(snapshots),
                    available=False,
                )
            )
        return FroniusPVCoordinatorData(tuple(devices))

    def _poll_devices(self) -> FroniusPVCoordinatorData:
        """Poll every device without retaining stale data after partial failure."""
        devices = []
        for device_id, transport in self.transports.items():
            discovered = self.discovered_models_by_device.get(device_id, ())
            try:
                if not discovered:
                    owner = getattr(transport, "endpoint", transport)
                    owner.connect()
                    discovered = SunSpecDiscovery(transport).discover()
                    self.discovered_models_by_device[device_id] = discovered
                decoded = self._poll_device(transport, discovered)
            except ModbusTransportError, SunSpecDiscoveryError:
                _LOGGER.debug(
                    "Failed to poll Modbus device ID %s", device_id, exc_info=True
                )
                devices.append(
                    DeviceSnapshot(device_id, discovered, (), available=False)
                )
            else:
                devices.append(
                    DeviceSnapshot(
                        device_id,
                        discovered,
                        decoded,
                        available=any(model.available for model in decoded),
                    )
                )
                previous = {
                    item["model"]["base_address"]: item
                    for item in self.topology.get(str(device_id), ())
                }
                by_address = {item.discovered.base_address: item for item in decoded}
                records = []
                for model in discovered:
                    snapshot = by_address.get(model.base_address)
                    if snapshot is not None and snapshot.available:
                        records.append(model_topology(model, snapshot.decoded))
                    else:
                        records.append(
                            previous.get(model.base_address, model_topology(model))
                        )
                self.topology = {**self.topology, str(device_id): records}
        return FroniusPVCoordinatorData(tuple(devices))

    @staticmethod
    def _poll_device(
        transport: ModbusDeviceTransport,
        discovered_models: tuple[DiscoveredModel, ...],
    ) -> tuple[DecodedModelSnapshot, ...]:
        """Decode all supported models for one device context."""
        decoded_models = []
        for discovered in discovered_models:
            definition = get_model_definition(discovered.model_id)
            if definition is None:
                continue
            try:
                payload = read_holding_registers_chunked(
                    transport,
                    discovered.base_address,
                    discovered.length,
                )
                decoded = decode_model(definition, payload)
            except ModbusTransportError, ValueError:
                _LOGGER.debug(
                    "Failed to read SunSpec model %s at address %s",
                    discovered.model_id,
                    discovered.base_address,
                    exc_info=True,
                )
                # Preserve occurrence coordinates without retaining old values.
                decoded_models.append(
                    DecodedModelSnapshot(
                        discovered,
                        definition,
                        DecodedModel({}, {}),
                        available=False,
                    )
                )
            else:
                decoded_models.append(
                    DecodedModelSnapshot(discovered, definition, decoded)
                )
        return tuple(decoded_models)

    async def async_close(self) -> None:
        """Close each unique synchronous endpoint in the executor."""
        async with self._io_lock:
            await self.hass.async_add_executor_job(self._close_transports)

    def _close_transports(self) -> None:
        """Attempt every unique endpoint close and raise the first error."""
        first_error = None
        closed: set[int] = set()
        for transport in self.transports.values():
            owner = getattr(transport, "endpoint", transport)
            if id(owner) in closed:
                continue
            closed.add(id(owner))
            try:
                owner.close()
            except ModbusTransportError as err:
                if first_error is None:
                    first_error = err
        if first_error is not None:
            raise first_error
