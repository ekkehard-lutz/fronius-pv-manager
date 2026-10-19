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

from .const import (
    CONF_FAST_SCAN_INTERVAL,
    CONF_SLOW_SCAN_INTERVAL,
    DEFAULT_FAST_SCAN_INTERVAL,
    DEFAULT_SLOW_SCAN_INTERVAL,
    DOMAIN,
    FAST_MODEL_IDS,
)
from .diagnostic_logging import (
    SUMMARY_SECONDS,
    Diagnostics,
    diagnostic_observer,
    error_reason,
)
from .model_decoder import DecodedModel, decode_model
from .models import DiscoveredModel, SunSpecModelDefinition
from .register_maps import get_model_definition
from .solar_api import SolarAPI, SolarState
from .storage_control import StorageControl
from .sunspec import SunSpecDiscovery, SunSpecDiscoveryError
from .topology import CONF_TOPOLOGY, model_topology, restore_model, validate_topology
from .transport import (
    MAX_HOLDING_REGISTERS_PER_READ,
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
    read_failed: bool = False


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
        self.fast_interval = entry.options.get(
            CONF_FAST_SCAN_INTERVAL, DEFAULT_FAST_SCAN_INTERVAL
        )
        self.slow_interval = entry.options.get(
            CONF_SLOW_SCAN_INTERVAL, DEFAULT_SLOW_SCAN_INTERVAL
        )
        self._slow_deadlines: dict[int, float] = {}
        self._force_slow = False
        self._measurement_data = None
        self.measurement_epochs = {}
        self.slow_publication_devices = set()
        self.measurement_time = 0.0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.fast_interval),
        )
        if not transports:
            raise ValueError("at least one Modbus device transport is required")
        self.transports = MappingProxyType(dict(transports))
        self.discovered_models_by_device: dict[int, tuple[DiscoveredModel, ...]] = {}
        self.entry = entry
        self.diagnostics = Diagnostics(lambda: entry.options, entry_id=entry.entry_id)
        for transport in self.transports.values():
            owner = getattr(transport, "endpoint", transport)
            if hasattr(owner, "set_diagnostics"):
                owner.set_diagnostics(self.diagnostics)
        self.solar_api = SolarAPI(hass, entry.data.get("host", "localhost"))
        self.solar_state = SolarState()
        # Persisted structure is for entity construction, never write authority.
        self.topology = validate_topology(entry.data.get(CONF_TOPOLOGY, {}))
        self._live_generations = {}
        self._discovery_revisions = {}
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
    def measurement_data(self):
        """Retained sensor readings; never a source of write authority."""
        return self.data if self._measurement_data is None else self._measurement_data

    def _retain_measurements(self, data, incompatible, validated):
        previous = {d.device_id: d for d in self.measurement_data.devices}
        devices = []
        for device in data.devices:
            old = previous.get(device.device_id)
            changed = device.device_id in incompatible or (
                old is not None
                and old.decoded_models
                and (device.discovered_models or device.device_id in validated)
                and device.discovered_models != old.discovered_models
            )
            if old is not None:
                # A successfully decoded identity/channel change proves replacement.
                for snapshot in device.decoded_models:
                    prior = next(
                        (
                            s
                            for s in old.decoded_models
                            if s.discovered == snapshot.discovered
                        ),
                        None,
                    )
                    if snapshot.available and prior is not None and prior.available:
                        if self._reading_identity(snapshot) != self._reading_identity(
                            prior
                        ):
                            changed = True
            old_snapshots = old.decoded_models if old else ()
            if changed:
                self.measurement_epochs[device.device_id] = (
                    self.measurement_epochs.get(device.device_id, 0) + 1
                )
                old = None
            if device.device_id in incompatible:
                devices.append(DeviceSnapshot(device.device_id, (), (), False))
                continue
            if not device.decoded_models and old is not None:
                devices.append(old)
                continue
            cached = {s.discovered: s for s in old.decoded_models} if old else {}
            snapshots = tuple(
                DecodedModelSnapshot(
                    s.discovered, s.definition, DecodedModel({}, {}), available=False
                )
                if changed and any(s is prior for prior in old_snapshots)
                else cached[s.discovered]
                if s.read_failed and s.discovered in cached
                else s
                for s in device.decoded_models
            )
            devices.append(
                DeviceSnapshot(
                    device.device_id,
                    device.discovered_models,
                    snapshots,
                    any(s.available for s in snapshots),
                )
            )
        self._measurement_data = FroniusPVCoordinatorData(tuple(devices))

    @staticmethod
    def _reading_identity(snapshot):
        if snapshot.discovered.model_id == 1:
            return tuple(snapshot.decoded.fixed.get(k) for k in ("Mn", "Md", "SN"))
        if snapshot.discovered.model_id == 160:
            return tuple(
                tuple(m.values.get(k) for k in ("ID", "IDStr"))
                for m in snapshot.decoded.repeating.get("module", ())
            )
        return None

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

    def write_authority(self, device_id):
        """Identify the live session and discovery that authorize physical plans."""
        models = self.live_models(device_id)
        authority = (
            (
                self._generation(device_id),
                self._discovery_revisions.get(device_id, 0),
                models,
            )
            if models
            else None
        )
        self.diagnostics.transition(
            ("device", device_id),
            "control",
            "write_authority_changed",
            authority,
            device_id=device_id,
            connection_generation=self._generation(device_id),
            discovery_revision=self._discovery_revisions.get(device_id, 0),
            result="valid" if authority else "invalid",
        )
        return authority

    def _discover_device(self, device_id, transport):
        self._discovery_revisions[device_id] = (
            self._discovery_revisions.get(device_id, 0) + 1
        )
        self.discovered_models_by_device.pop(device_id, None)
        owner = getattr(transport, "endpoint", transport)
        scope = ("device", device_id)
        endpoint_scope = ("endpoint", id(owner))
        recovering = self.diagnostics.active(scope) or self.diagnostics.active(
            endpoint_scope
        )
        reason = (
            "communication_recovery"
            if recovering
            else (
                "initial"
                if self._discovery_revisions[device_id] == 1
                else "scheduled_renewal"
                if time.monotonic() >= self._discovery_deadlines.get(device_id, 0)
                else "session_change"
            )
        )
        if recovering:
            self.diagnostics.failure(
                scope,
                "sunspec",
                "rediscovery_started",
                reason=reason,
                device_id=device_id,
            )
        self.diagnostics.event(
            3,
            "sunspec",
            "discovery_started",
            scope=scope,
            interval=SUMMARY_SECONDS,
            device_id=device_id,
            reason=reason,
        )
        try:
            owner.connect()
            discovered = SunSpecDiscovery(transport).discover()
        except (ModbusTransportError, SunSpecDiscoveryError) as err:
            self.diagnostics.failure(
                scope,
                "sunspec",
                "discovery_failed",
                reason=error_reason(err),
                device_id=device_id,
                connection_generation=self._generation(device_id),
            )
            raise
        if recovering:
            self.diagnostics.failure(
                scope,
                "sunspec",
                "discovery_recovered",
                reason="validated",
                device_id=device_id,
                connection_generation=self._generation(device_id),
                discovery_revision=self._discovery_revisions[device_id],
            )
        self.diagnostics.event(
            3,
            "sunspec",
            "discovery_completed",
            scope=scope,
            interval=SUMMARY_SECONDS,
            device_id=device_id,
            discovery_revision=self._discovery_revisions[device_id],
            result="validated",
            model_count=len(discovered),
        )
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

    async def async_request_refresh(self) -> None:
        """Include control/settings readback in explicit (including write) refreshes."""
        self._force_slow = True
        await super().async_request_refresh()

    async def _async_update_data(self) -> FroniusPVCoordinatorData:
        """Poll due whole models and publish one coherent snapshot."""
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
        except Exception as err:
            self.diagnostics.event(
                0, "coordinator", "runtime_error", reason=type(err).__name__
            )
            raise
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
        """Read FAST each refresh; retain untouched SLOW snapshots by identity."""
        devices = []
        incompatible = set()
        validated = set()
        self.slow_publication_devices = set()
        force_slow = self._force_slow
        self._force_slow = False
        for device_id, transport in self.transports.items():
            started = time.monotonic()
            discovered = self.live_models(device_id)
            rediscover = not discovered
            deadline = self._slow_deadlines.get(device_id, started)
            if started >= deadline:
                self.slow_publication_devices.add(device_id)
            slow_due = force_slow or rediscover or started >= deadline
            previous_device = next(
                (
                    device
                    for device in self.data.devices
                    if device.device_id == device_id
                ),
                None,
            )
            cached = {
                model.discovered: model
                for model in (previous_device.decoded_models if previous_device else ())
            }
            selected = ()
            decoded = ()
            success = False
            try:
                if rediscover:
                    discovered = self._discover_device(device_id, transport)
                    validated.add(device_id)
                selected = tuple(
                    model
                    for model in discovered
                    if get_model_definition(model.model_id) is not None
                    and (
                        slow_due
                        or model.model_id in FAST_MODEL_IDS
                        or model not in cached
                    )
                )
                refreshed = {
                    model.discovered: model
                    for model in self._poll_device(transport, selected)
                }
                decoded = tuple(
                    refreshed[model] if model in refreshed else cached[model]
                    for model in discovered
                    if model in refreshed or model in cached
                )
                success = all(model.available for model in decoded)
            except (ModbusTransportError, SunSpecDiscoveryError) as err:
                if isinstance(err, SunSpecDiscoveryError):
                    incompatible.add(device_id)
                self.discovered_models_by_device.pop(device_id, None)
                self.diagnostics.failure(
                    ("device", device_id),
                    "modbus",
                    "communication_failure",
                    reason=error_reason(err),
                    device_id=device_id,
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
                        # Cached identity/settings cannot keep an offline device
                        # available when every attempted dynamic read failed.
                        available=(
                            any(model.available for model in refreshed.values())
                            if refreshed
                            else bool(previous_device and previous_device.available)
                        ),
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
            finally:
                # Anchor deadlines to elapsed time, not a modulo cycle count or
                # the end of a read. Missed periods are skipped, never replayed.
                if started >= deadline:
                    self._slow_deadlines[device_id] = (
                        deadline
                        + (int((started - deadline) // self.slow_interval) + 1)
                        * self.slow_interval
                    )
                if self.diagnostics.enabled(3):
                    self.diagnostics.event(
                        3,
                        "coordinator",
                        "poll_summary",
                        scope=("device", device_id),
                        interval=SUMMARY_SECONDS,
                        device_id=device_id,
                        cadence="FAST+SLOW" if slow_due else "FAST",
                        models=[
                            (m.model_id, m.base_address, m.length) for m in selected
                        ],
                        planned_reads=sum(
                            (m.length + MAX_HOLDING_REGISTERS_PER_READ - 1)
                            // MAX_HOLDING_REGISTERS_PER_READ
                            for m in selected
                        ),
                        planned_registers=sum(m.length for m in selected),
                        discovery=rediscover,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                        result="success" if success else "failed",
                        connection_generation=self._generation(device_id),
                    )
        data = FroniusPVCoordinatorData(tuple(devices))
        self.measurement_time = time.monotonic()
        self._retain_measurements(data, incompatible, validated)
        self._diagnose_live(data)
        return data

    def _poll_device(
        self,
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
            except (ModbusTransportError, ValueError) as err:
                device_id = next(
                    (
                        unit
                        for unit, view in self.transports.items()
                        if view is transport
                    ),
                    None,
                )
                reason = error_reason(err)
                if isinstance(err, ModbusTransportError):
                    self.diagnostics.failure(
                        ("device", device_id),
                        "modbus",
                        "communication_failure",
                        reason=reason,
                        device_id=device_id,
                    )
                if isinstance(err, ValueError):
                    self.diagnostics.event(
                        0,
                        "sunspec",
                        "protocol_error",
                        reason="decode_error",
                        scope=("model", device_id, discovered.model_id),
                        interval=SUMMARY_SECONDS,
                        device_id=device_id,
                        model_id=discovered.model_id,
                    )
                self.diagnostics.event(
                    3,
                    "sunspec",
                    "model_read_failed",
                    scope=("model", device_id, discovered.model_id),
                    interval=SUMMARY_SECONDS,
                    device_id=device_id,
                    model_id=discovered.model_id,
                    reason=reason,
                )
                # Live safety data remains invalid; sensor retention is separate.
                decoded_models.append(
                    DecodedModelSnapshot(
                        discovered,
                        definition,
                        DecodedModel({}, {}),
                        available=False,
                        read_failed=isinstance(err, ModbusTransportError),
                    )
                )
            else:
                decoded_models.append(
                    DecodedModelSnapshot(discovered, definition, decoded)
                )
        return tuple(decoded_models)

    @diagnostic_observer
    def _diagnose_live(self, data):
        # Observe current I/O outcomes, never the retained sensor view.
        owners = {}
        for device in data.devices:
            unit = device.device_id
            transport = self.transports[unit]
            owner = getattr(transport, "endpoint", transport)
            owners.setdefault(id(owner), []).append(device)
            scope = ("device", unit)
            complete = bool(device.decoded_models) and all(
                s.available for s in device.decoded_models
            )
            if complete and self.live_models(unit):
                self.diagnostics.recover(
                    scope,
                    "modbus",
                    "communication_recovered",
                    device_id=unit,
                    connection_generation=self._generation(unit),
                    discovery_revision=self._discovery_revisions.get(unit, 0),
                )
            if self.diagnostics.enabled(2):
                self.diagnostics.transition(
                    scope,
                    "coordinator",
                    "device_availability_changed",
                    device.available,
                    device_id=unit,
                    result="live" if device.available else "offline",
                )
                if (
                    unit in self.discovered_models_by_device
                    and self._live_generations.get(unit) == self._generation(unit)
                ):
                    self.diagnostics.transition(
                        scope,
                        "sunspec",
                        "topology_changed",
                        device.discovered_models,
                        device_id=unit,
                    )
                self.write_authority(unit)
                for snapshot in device.decoded_models:
                    if not snapshot.available:
                        continue
                    mid = snapshot.discovered.model_id
                    if (
                        sum(s.discovered.model_id == mid for s in device.decoded_models)
                        != 1
                    ):
                        # Ambiguous sources cannot establish a physical transition.
                        continue
                    field = {103: "St", 124: "ChaSt"}.get(mid)
                    if (
                        field is not None
                        and (value := snapshot.decoded.fixed.get(field)) is not None
                    ):
                        if value.value is not None:
                            self.diagnostics.transition(
                                ("model", unit, mid),
                                "coordinator",
                                "operating_state_changed",
                                value.value,
                                device_id=unit,
                                model_id=mid,
                                state=value.value,
                            )
                    if mid == 160:
                        self.diagnostics.transition(
                            scope,
                            "sunspec",
                            "capabilities_changed",
                            self._reading_identity(snapshot),
                            device_id=unit,
                        )
        for owner_id, devices in owners.items():
            if all(
                d.decoded_models
                and all(s.available for s in d.decoded_models)
                and self.live_models(d.device_id)
                for d in devices
            ):
                self.diagnostics.recover(
                    ("endpoint", owner_id), "modbus", "endpoint_recovered"
                )

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
            self.diagnostics.event(
                0, "coordinator", "lifecycle_error", reason="transport_close_failed"
            )

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
