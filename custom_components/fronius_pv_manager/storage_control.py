"""Persistent watt constraints and Model 124 signed power-window translation."""

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from fractions import Fraction

from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .codec import encode_register_value
from .register_maps import MODEL_124
from .register_reader import read_register
from .write_runtime import WriteInvalidValueError, WriteSequenceError

_LOGGER = logging.getLogger(__name__)
REMOTE_LEASE_SECONDS = 90


@dataclass(frozen=True)
class RemoteLease:
    """Runtime-only coordination identity and monotonic deadline."""

    owner_id: str
    expires_at: float


@dataclass(frozen=True)
class PowerSettings:
    """Independent user settings; never reconstructed from rate registers."""

    minimum_charge_power: float = 0
    maximum_charge_power: float = 0
    minimum_discharge_power: float = 0
    maximum_discharge_power: float = 0


@dataclass(frozen=True)
class PreRemoteSnapshot:
    """One lease's immutable user configuration, never serialized."""

    minimum_reserve: float
    grid_charging_allowed: bool
    power_settings: PowerSettings


@dataclass(frozen=True)
class RemoteCleanup:
    """Runtime-only verified progress from the latest cleanup attempt."""

    verified_steps: int = 0
    failed_step: str | None = None


def watts_to_percent(watts: float, reference: float) -> float:
    """Convert against decoded WChaMax, without using technical battery limits."""
    if (
        type(reference) not in (int, float)
        or not math.isfinite(reference)
        or reference <= 0
    ):
        raise ServiceValidationError("WChaMax must be available and positive")
    if (
        type(watts) not in (int, float)
        or not math.isfinite(watts)
        or not 0 <= watts <= reference
    ):
        raise ServiceValidationError("power must be between 0 and WChaMax")
    return float(Decimal(str(watts)) * 100 / Decimal(str(reference)))


def percent_to_watts(percent: float, reference: float) -> float:
    """Convert a nonnegative magnitude from percent of WChaMax to watts."""
    watts_to_percent(0, reference)
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ServiceValidationError("percentage must be between 0 and 100")
    return float(Decimal(str(percent)) * Decimal(str(reference)) / 100)


_RATE_REGISTER = next(
    register for register in MODEL_124.registers if register.name == "InWRte"
)


def _rate_resolution(scale_factor: int) -> Fraction:
    """Return an exact percent step compatible with the complete HLC sequence."""
    if type(scale_factor) is not int or not -32767 <= scale_factor <= 32767:
        raise ServiceValidationError(
            "storage rate scale factor is unavailable or invalid"
        )
    try:
        # The neutral transition requires both signed endpoints to be encodable.
        # Let the existing register model/codec enforce representation and sentinels.
        encode_register_value(_RATE_REGISTER, 100, scale_factor)
        encode_register_value(_RATE_REGISTER, -100, scale_factor)
    except ValueError as err:
        raise ServiceValidationError(
            "storage rate scale cannot represent +/-100%"
        ) from err
    return Fraction(10) ** scale_factor


def validate_power_settings(settings: PowerSettings, reference: float) -> None:
    """Validate semantic settings without requiring exact register representation."""
    for value in asdict(settings).values():
        watts_to_percent(value, reference)
    for direction in ("charge", "discharge"):
        if getattr(settings, f"minimum_{direction}_power") > getattr(
            settings, f"maximum_{direction}_power"
        ):
            raise ServiceValidationError("minimum power exceeds maximum power")
    if settings.minimum_charge_power and settings.minimum_discharge_power:
        raise ServiceValidationError(
            "charging and discharging minima cannot both be positive"
        )


def power_window(
    settings: PowerSettings,
    reference: float,
    mode: str,
    scale_factor: int | None = None,
):
    """Quantize magnitudes inward, then map to signed [-InWRte, OutWRte]."""
    if mode == "automatic":
        return {"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}
    if mode != "manual":
        raise ServiceValidationError("unknown storage operating mode")
    validate_power_settings(settings, reference)
    resolution = _rate_resolution(scale_factor)

    def magnitude(watts: float, *, minimum: bool) -> float:
        # Exact rational arithmetic avoids binary/decimal division rounding at
        # raw integer boundaries. Round the magnitude before applying its sign.
        raw = Fraction(str(watts)) * 100 / (Fraction(str(reference)) * resolution)
        integral = math.ceil(raw) if minimum else math.floor(raw)
        return float(integral * resolution)

    target = {
        "StorCtl_Mod": 3,
        "InWRte": (
            -magnitude(settings.minimum_discharge_power, minimum=True)
            if settings.minimum_discharge_power
            else magnitude(settings.maximum_charge_power, minimum=False)
        ),
        "OutWRte": (
            -magnitude(settings.minimum_charge_power, minimum=True)
            if settings.minimum_charge_power
            else magnitude(settings.maximum_discharge_power, minimum=False)
        ),
    }
    if -target["InWRte"] > target["OutWRte"]:
        raise ServiceValidationError(
            "no representable power window satisfies the constraints"
        )
    return target


_POWER_REGISTERS = frozenset({"StorCtl_Mod", "InWRte", "OutWRte"})


def valid_power_state(state: object) -> bool:
    """Accept documented modes and finite boundaries with a valid active window."""
    if not isinstance(state, Mapping) or set(state) != _POWER_REGISTERS:
        return False
    mode = state["StorCtl_Mod"]
    if type(mode) is not int or mode not in (0, 1, 2, 3):
        return False
    for name in ("InWRte", "OutWRte"):
        value = state[name]
        if type(value) not in (int, float) or not math.isfinite(value):
            return False
        if not -100 <= value <= 100:
            return False
    # Disabled boundaries do not constrain the active power interval.
    return mode != 3 or -state["InWRte"] <= state["OutWRte"]


def classify_storage_control(current: object, last_target: object) -> str:
    """Compare confirmed state without claiming who wrote it or requesting I/O."""
    if not valid_power_state(current):
        return "unknown"
    if current == {"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}:
        return "automatic"
    if not valid_power_state(last_target):
        return "unknown"
    if current == last_target and current["StorCtl_Mod"] == 3:
        return "manual_hlc"
    return "low_level_override"


class StorageControl:
    """Own persisted settings and serialize concurrent high-level requests."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.store = Store(
            coordinator.hass,
            1,
            f"fronius_pv_manager.{coordinator.entry.entry_id}.storage",
            atomic_writes=True,
        )
        self.settings: dict[str, dict] = {}
        self.modes: dict[str, str] = {}
        self.last_targets: dict[str, dict[str, int | float]] = {}
        self.lock = asyncio.Lock()
        self._leases: dict[int, RemoteLease] = {}
        self._pre_remote: dict[int, PreRemoteSnapshot] = {}
        self._cleanup: dict[int, RemoteCleanup] = {}
        self._watchdogs = {}
        self._watchdog_tasks: set[asyncio.Task] = set()
        self._closed = False

    async def async_load(self):
        """Load integration-owned settings before entities are set up."""
        saved = await self.store.async_load() or {}
        if "settings" not in saved:
            # Compatibility with the initial watt-only development state.
            # Never infer an applied target or selected mode from those values.
            self.settings = saved
            return
        self.settings = saved["settings"]
        self.modes = {
            device: "automatic" if mode == "remote" else mode
            for device, mode in saved.get("modes", {}).items()
            if mode in ("automatic", "manual", "remote")
        }
        self.last_targets = {
            device: target
            for device, target in saved.get("last_targets", {}).items()
            if valid_power_state(target)
            and (
                target["StorCtl_Mod"] == 3
                or target == {"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}
            )
        }

    def snapshot(self, device_id):
        """Require one unambiguous, available storage model."""
        for device in self.coordinator.data.devices:
            if device.device_id == device_id and device.available:
                models = [
                    model
                    for model in device.decoded_models
                    if model.discovered.model_id == 124
                ]
                if len(models) == 1 and models[0].available:
                    return models[0].decoded.fixed
        raise ServiceValidationError("storage model is unavailable or ambiguous")

    def reference(self, device_id):
        value = self.snapshot(device_id)["WChaMax"].value
        watts_to_percent(0, value)
        return value

    def rate_scale_factor(self, device_id):
        """Read the currently decoded rate scale; writer preflight rechecks it live."""
        return self.snapshot(device_id)["InOutWRte_SF"].value

    def validate_power_resolution(self, device_id) -> None:
        """Retain availability validation independently of the semantic UI step."""
        self.reference(device_id)
        _rate_resolution(self.rate_scale_factor(device_id))

    def values(self, device_id):
        saved = self.settings.get(str(device_id))
        if saved is not None:
            return PowerSettings(**saved)
        reference = self.reference(device_id)
        return PowerSettings(0, math.floor(reference), 0, math.floor(reference))

    def mode(self, device_id):
        """Return the selected HLC mode, never a mirror of raw control bits."""
        return self.modes.get(str(device_id), "automatic")

    def status(self, device_id) -> str:
        """Classify only available confirmed register data; never enforce a profile."""
        if not self.coordinator.last_update_success:
            return "unknown"
        try:
            fixed = self.snapshot(device_id)
            current = {
                name: fixed[name].raw if name == "StorCtl_Mod" else fixed[name].value
                for name in _POWER_REGISTERS
            }
        except ServiceValidationError, KeyError:
            return "unknown"
        return classify_storage_control(current, self.last_targets.get(str(device_id)))

    async def async_change(self, device_id, *, field=None, value=None, mode=None):
        """Validate, apply if active, then persist settings after verified success."""
        async with self.lock:
            await self._manual_access(device_id)
            if mode is not None and mode not in ("automatic", "manual"):
                raise ServiceValidationError(
                    "remote mode requires programmatic ownership"
                )
            settings = self.values(device_id)
            if field is not None:
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value != int(value)
                ):
                    raise ServiceValidationError("power settings require whole watts")
                value = int(value)
                changes = {field: value}
                opposite_minimum = {
                    "minimum_charge_power": "minimum_discharge_power",
                    "minimum_discharge_power": "minimum_charge_power",
                }.get(field)
                if value > 0 and opposite_minimum is not None:
                    changes[opposite_minimum] = 0
                # Normalize one complete semantic transition before validation or I/O.
                settings = replace(settings, **changes)
            selected_mode = mode if mode is not None else self.mode(device_id)
            await self._apply_locked(
                device_id,
                settings,
                selected_mode,
                apply=mode is not None or selected_mode == "manual",
                validate=field is not None or selected_mode != "automatic",
            )

    async def async_set_power_window(
        self, device_id: int, settings: PowerSettings
    ) -> None:
        """Apply one complete manual profile; requires no remote ownership.

        Unlike a directional field edit, a full state has no ordering/precedence:
        both positive minima are rejected. This operation enters manual mode.
        """
        async with self.lock:
            await self._manual_access(device_id)
            await self._apply_locked(device_id, settings, "manual", apply=True)

    async def _apply_locked(
        self,
        device_id,
        settings,
        mode,
        *,
        apply,
        validate=True,
        persisted_profile=None,
        before_write=None,
    ):
        """Shared HLC commit path. Caller holds the semantic state lock."""
        if validate:
            if not isinstance(settings, PowerSettings):
                raise ServiceValidationError(
                    "a complete PowerSettings object is required"
                )
            for value in asdict(settings).values():
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value != int(value)
                ):
                    raise ServiceValidationError("power settings require whole watts")
            settings = PowerSettings(
                **{key: int(value) for key, value in asdict(settings).items()}
            )
            validate_power_settings(settings, self.reference(device_id))
        if mode in ("manual", "remote"):
            target = power_window(
                settings,
                self.reference(device_id),
                "manual",
                self.rate_scale_factor(device_id),
            )
        else:
            target = power_window(settings, 0, "automatic")
        if apply:
            # One validated, locked sequence; not atomic on the Modbus device.
            sequence = [
                (124, "StorCtl_Mod", 0),
                (124, "InWRte", 100),
                (124, "OutWRte", 100),
            ]
            if mode in ("manual", "remote"):
                sequence.extend(
                    [
                        (124, "InWRte", target["InWRte"]),
                        (124, "OutWRte", target["OutWRte"]),
                        (124, "StorCtl_Mod", 3),
                    ]
                )
            await self.coordinator.write_runtime.async_write_sequence(
                device_id,
                sequence,
                **({"before_write": before_write} if before_write else {}),
            )
        updated = {**self.settings, str(device_id): asdict(settings)}
        modes = {**self.modes, str(device_id): mode}
        targets = dict(self.last_targets)
        if apply:
            targets[str(device_id)] = target
        persisted = updated
        if mode == "remote":
            if persisted_profile is None and device_id in self._pre_remote:
                persisted_profile = self._pre_remote[device_id].power_settings
            if persisted_profile is not None:
                persisted = {**updated, str(device_id): asdict(persisted_profile)}
        # Other devices may also be under temporary remote control.
        for other, snapshot in self._pre_remote.items():
            if other != device_id:
                persisted = {**persisted, str(other): asdict(snapshot.power_settings)}
        await self.store.async_save(
            {"settings": persisted, "modes": modes, "last_targets": targets}
        )
        self.settings = updated
        self.modes = modes
        self.last_targets = targets
        self.coordinator.async_update_listeners()

    def _now(self) -> float:
        return self.coordinator.hass.loop.time()

    def remote_owner(self, device_id: int) -> str | None:
        """Return only live ownership; owner IDs are not security credentials."""
        lease = self._leases.get(device_id)
        return (
            lease.owner_id
            if lease
            and device_id not in self._cleanup
            and self._now() < lease.expires_at
            else None
        )

    def _ensure_open(self):
        if self._closed:
            raise ServiceValidationError("storage control is unloading")

    @staticmethod
    def _validate_owner_id(owner_id):
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ServiceValidationError("a nonempty remote owner_id is required")

    def _require_owner(self, device_id, owner_id, *, live=True):
        self._ensure_open()
        self._validate_owner_id(owner_id)
        lease = self._leases.get(device_id)
        if lease is None or lease.owner_id != owner_id:
            raise ServiceValidationError("caller does not own remote storage control")
        if live and device_id in self._cleanup:
            raise ServiceValidationError("remote cleanup is pending; retry release")
        if live and self._now() >= lease.expires_at:
            raise ServiceValidationError("remote storage lease has expired")

    async def _manual_access(self, device_id):
        self._ensure_open()
        if device_id in self._leases:
            if self.remote_owner(device_id) is not None:
                raise ServiceValidationError(
                    "Remote storage control is active; this setting is managed "
                    "by the remote controller."
                )
            # An expired lease cannot be stolen or renewed. Finish its fail-safe
            # release before accepting a new manual action or acquisition.
            await self._expire_locked(device_id)

    async def async_acquire_remote_control(
        self,
        device_id: int,
        owner_id: str,
        settings: PowerSettings | None = None,
    ) -> None:
        """Acquire/renew ownership after applying a complete verified remote window."""
        async with self.lock:
            self._ensure_open()
            self._validate_owner_id(owner_id)
            owner = self.remote_owner(device_id)
            if owner is not None and owner != owner_id:
                raise ServiceValidationError(
                    "remote storage control already has a live owner"
                )
            if device_id in self._leases and owner is None:
                await self._expire_locked(device_id)
            candidate = []
            user_profile = self.values(device_id)

            def capture(transport, discovered):
                reserve = read_register(
                    transport, discovered, 124, "MinRsvPct"
                ).value.value
                grid = read_register(transport, discovered, 124, "ChaGriSet").value.raw
                self._validate_policy_setting("MinRsvPct", reserve)
                self._validate_policy_setting("ChaGriSet", grid)
                candidate.append(PreRemoteSnapshot(reserve, bool(grid), user_profile))

            first_acquire = device_id not in self._pre_remote
            await self._apply_locked(
                device_id,
                user_profile if settings is None else settings,
                "remote",
                apply=True,
                persisted_profile=user_profile if first_acquire else None,
                before_write=capture if first_acquire else None,
            )
            if first_acquire:
                self._pre_remote[device_id] = candidate[0]
            self._renew(device_id, owner_id)

    async def async_remote_heartbeat(self, device_id: int, owner_id: str) -> None:
        """Renew a live owner's deadline without reading/writing Modbus."""
        async with self.lock:
            self._require_owner(device_id, owner_id)
            self._renew(device_id, owner_id)

    async def async_set_remote_power_window(
        self,
        device_id: int,
        owner_id: str,
        settings: PowerSettings,
    ) -> None:
        """Apply a complete owner-supplied state and renew only after success."""
        async with self.lock:
            self._require_owner(device_id, owner_id)
            await self._apply_locked(device_id, settings, "remote", apply=True)
            self._renew(device_id, owner_id)

    async def async_write_policy_setting(self, device_id, name, value):
        """Write a manual reserve/grid HLC setting under the ownership lock."""
        async with self.lock:
            await self._manual_access(device_id)
            await self._write_policy_setting(device_id, name, value)

    @staticmethod
    def _validate_policy_setting(name, value):
        if name == "MinRsvPct":
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 5 <= value <= 100
            ):
                raise ServiceValidationError(
                    "minimum reserve must be between 5 and 100"
                )
        elif name == "ChaGriSet":
            if type(value) is not int or value not in (0, 1):
                raise ServiceValidationError("grid charging permission must be 0 or 1")
        else:
            raise ServiceValidationError("unknown storage policy setting")

    async def _write_policy_setting(self, device_id, name, value):
        self._validate_policy_setting(name, value)
        try:
            await self.coordinator.write_runtime.async_write(
                device_id, 124, name, value
            )
        except WriteInvalidValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_set_remote_minimum_reserve(
        self, device_id: int, owner_id: str, value: float
    ) -> None:
        """Write reserve through policy and renew only after verified success."""
        async with self.lock:
            self._require_owner(device_id, owner_id)
            await self._write_policy_setting(device_id, "MinRsvPct", value)
            self._renew(device_id, owner_id)

    async def async_set_remote_grid_charging_allowed(
        self, device_id: int, owner_id: str, enabled: bool
    ) -> None:
        """Write grid permission through policy and renew only after success."""
        async with self.lock:
            self._require_owner(device_id, owner_id)
            if type(enabled) is not bool:
                raise ServiceValidationError("enabled must be a boolean")
            await self._write_policy_setting(device_id, "ChaGriSet", int(enabled))
            self._renew(device_id, owner_id)

    async def async_release_remote_control(self, device_id: int, owner_id: str) -> None:
        """Release to verified automatic, also allowing recovery of an expired lease."""
        async with self.lock:
            self._require_owner(device_id, owner_id, live=False)
            await self._release_locked(device_id)

    async def _release_locked(self, device_id):
        snapshot = self._pre_remote.get(device_id)
        if snapshot is None:
            raise ServiceValidationError("pre-remote snapshot is missing")
        self._validate_policy_setting("MinRsvPct", snapshot.minimum_reserve)
        grid = int(snapshot.grid_charging_allowed)
        self._validate_policy_setting("ChaGriSet", grid)
        self._cancel_watchdog(device_id)
        self._cleanup[device_id] = RemoteCleanup()
        sequence = [
            (124, "StorCtl_Mod", 0),
            (124, "InWRte", 100),
            (124, "OutWRte", 100),
            (124, "MinRsvPct", snapshot.minimum_reserve),
            (124, "ChaGriSet", grid),
        ]
        try:
            await self.coordinator.write_runtime.async_write_sequence(
                device_id, sequence, safety_prefix=3
            )
        except WriteSequenceError as err:
            self._cleanup[device_id] = RemoteCleanup(
                len(err.completed), err.failed_register
            )
            if len(err.completed) >= 3:
                self._record_automatic(device_id)
            raise
        except Exception:
            self._cleanup[device_id] = RemoteCleanup(0, "preflight")
            raise
        self._record_automatic(device_id)
        self._cleanup[device_id] = RemoteCleanup(5, "persistence")
        # Saved profile only: NEVER reapply the old manual power window.
        await self._apply_locked(
            device_id, snapshot.power_settings, "automatic", apply=False, validate=False
        )
        self._leases.pop(device_id, None)
        self._pre_remote.pop(device_id, None)
        self._cleanup.pop(device_id, None)

    def _record_automatic(self, device_id):
        """Record verified safety even when later policy/persistence steps fail."""
        self.modes[str(device_id)] = "automatic"
        self.last_targets[str(device_id)] = power_window(None, 0, "automatic")
        self.coordinator.async_update_listeners()

    async def _expire_locked(self, device_id):
        owner_id = self._leases[device_id].owner_id
        await self._release_locked(device_id)
        _LOGGER.warning(
            "Remote storage lease expired for device %s (owner %s); "
            "automatic mode restored",
            device_id,
            owner_id,
        )

    def _cancel_watchdog(self, device_id):
        if cancel := self._watchdogs.pop(device_id, None):
            cancel()

    def _renew(self, device_id, owner_id):
        self._leases[device_id] = RemoteLease(
            owner_id, self._now() + REMOTE_LEASE_SECONDS
        )
        self._schedule_watchdog(device_id, REMOTE_LEASE_SECONDS)

    def _schedule_watchdog(self, device_id, delay):
        self._cancel_watchdog(device_id)
        if self._closed:
            return

        @callback
        def expired(_now):
            if self._closed:
                return
            task = self.coordinator.hass.async_create_task(
                self._async_watchdog(device_id)
            )
            self._watchdog_tasks.add(task)
            task.add_done_callback(self._watchdog_tasks.discard)

        self._watchdogs[device_id] = async_call_later(
            self.coordinator.hass, delay, expired
        )

    async def _async_watchdog(self, device_id):
        async with self.lock:
            if (
                self._closed
                or device_id not in self._leases
                or device_id in self._cleanup
            ):
                return
            remaining = self._leases[device_id].expires_at - self._now()
            if remaining > 0:
                self._schedule_watchdog(device_id, remaining)
                return
            self._cancel_watchdog(device_id)
            try:
                await self._expire_locked(device_id)
            except Exception:
                # Retain the snapshot, reservation and verified cleanup progress.
                # Only an explicit later action can retry; never loop writes.
                _LOGGER.exception(
                    "Remote lease expired for device %s; automatic fallback failed; "
                    "cleanup pending: %s; runtime mode=%s",
                    device_id,
                    self._cleanup.get(device_id),
                    self.mode(device_id),
                )

    async def async_shutdown(self) -> None:
        """Cancel timers and drain pending HLC I/O before the transport is closed."""
        self._closed = True
        for device_id in tuple(self._watchdogs):
            self._cancel_watchdog(device_id)
        async with self.lock:
            self._leases.clear()
            self._pre_remote.clear()
            self._cleanup.clear()
        if self._watchdog_tasks:
            await asyncio.gather(*self._watchdog_tasks, return_exceptions=True)
