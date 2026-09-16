"""Home Assistant runtime coordinator for SunSpec discovery and polling."""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .model_decoder import DecodedModel, decode_model
from .models import DiscoveredModel, SunSpecModelDefinition
from .register_maps import get_model_definition
from .solar_api import SolarAPI, SolarState
from .storage_control import StorageControl
from .sunspec import SunSpecDiscovery, SunSpecDiscoveryError
from .topology import CONF_TOPOLOGY, model_topology, restore_model, validate_topology
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
        self.solar_api = SolarAPI(hass, entry.data.get("host", "localhost"))
        self.solar_state = SolarState()
        # Persisted structure is for entity construction, never write authority.
        self.topology = validate_topology(entry.data.get(CONF_TOPOLOGY, {}))
        self._live_generations = {}
        self._discovery_deadlines = {}
        self._closing = False
        self._closed = False
        self._stop_task = None
        self._update_tasks = set()
        self.stop_unsubscribe = None
        self.data = FroniusPVCoordinatorData(
            tuple(
                DeviceSnapshot(
                    device_id,
                    tuple(
                        restore_model(record)[0]
                        for record in self.topology.get(str(device_id), ())
                    ),
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

    async def async_run_io(self, target, *args, closing=False):
        """Serialize a worker and drain it before propagating cancellation.

        Repeated cancellation also cannot abandon the worker or release the lock.
        All synchronous operations on the shared client use this boundary.
        """
        async with self._io_lock:
            if not closing and (self._closing or self.hass.is_stopping):
                raise ServiceValidationError("Modbus runtime is shutting down")
            job = asyncio.ensure_future(self.hass.async_add_executor_job(target, *args))
            return await self._async_drain(job)

    @staticmethod
    async def _async_drain(job):
        """Preserve cancellation only after the owned task/future has finished."""
        cancelled = False
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not job.cancelled():
                job.exception()
            raise asyncio.CancelledError
        return job.result()

    def _generation(self, device_id):
        transport = self.transports[device_id]
        owner = getattr(transport, "endpoint", transport)
        return getattr(owner, "generation", 0)

    def live_models(self, device_id):
        """Return only current-session, bounded-age live discovery results."""
        if self._live_generations.get(device_id, 0) != self._generation(
            device_id
        ) or time.monotonic() >= self._discovery_deadlines.get(device_id, float("inf")):
            return ()
        return self.discovered_models_by_device.get(device_id, ())

    def _discover_device(self, device_id, transport):
        self.discovered_models_by_device.pop(device_id, None)
        owner = getattr(transport, "endpoint", transport)
        owner.connect()
        discovered = SunSpecDiscovery(transport).discover()
        self.discovered_models_by_device[device_id] = discovered
        self._live_generations[device_id] = self._generation(device_id)
        self._discovery_deadlines[device_id] = time.monotonic() + 300
        return discovered

    async def async_discover(self) -> None:
        """Discover live topology through the shared cancellation-safe boundary."""
        await self.async_run_io(self._connect_and_discover)

    def _connect_and_discover(self) -> dict[int, tuple[DiscoveredModel, ...]]:
        """Connect and discover every configured device synchronously."""
        for device_id, transport in self.transports.items():
            self._discover_device(device_id, transport)
        return dict(self.discovered_models_by_device)

    async def _async_update_data(self) -> FroniusPVCoordinatorData:
        """Poll and decode all supported models without blocking the event loop."""
        task = asyncio.current_task()
        self._update_tasks.add(task)
        try:
            data = await self.async_run_io(self._poll_devices)
            if self._closing:
                return data
            self.solar_state = await self.solar_api.async_poll()
            if self.entry.data.get(CONF_TOPOLOGY) != self.topology:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, CONF_TOPOLOGY: self.topology}
                )
            return data
        finally:
            self._update_tasks.discard(task)

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
            discovered = self.live_models(device_id)
            try:
                if not discovered:
                    discovered = self._discover_device(device_id, transport)
                decoded = self._poll_device(transport, discovered)
            except ModbusTransportError, SunSpecDiscoveryError:
                self.discovered_models_by_device.pop(device_id, None)
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
                if any(not model.available for model in decoded):
                    # Retry discovery once on the next scheduled poll, never here.
                    self.discovered_models_by_device.pop(device_id, None)
                previous = {
                    (
                        item["model"]["model_id"],
                        item["model"]["base_address"],
                        item["model"]["length"],
                    ): item
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
                            previous.get(
                                (model.model_id, model.base_address, model.length),
                                model_topology(model),
                            )
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

    async def async_stop(self, _event=None) -> None:
        """Idempotently stop without writing a remote release target."""
        self._closing = True
        if self.stop_unsubscribe is not None:
            self.stop_unsubscribe()
            self.stop_unsubscribe = None
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._async_stop())
        await self._async_drain(self._stop_task)

    async def _async_stop(self):
        await self.storage_control.async_shutdown()
        await self.async_shutdown()
        if self._update_tasks:
            await asyncio.gather(*self._update_tasks, return_exceptions=True)
        try:
            await self.async_close()
        except ModbusTransportError:
            _LOGGER.warning("Failed to close Fronius Modbus transport", exc_info=True)

    async def async_close(self) -> None:
        """Drain existing I/O, reject new operations, and close exactly once."""
        self._closing = True
        await self.async_run_io(self._close_transports, closing=True)

    def _close_transports(self) -> None:
        """Attempt every unique endpoint close and raise the first error."""
        if self._closed:
            return
        self._closed = True
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
