"""Persistent watt constraints and Model 124 signed power-window translation."""

import asyncio
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store


@dataclass(frozen=True)
class PowerSettings:
    """Independent user settings; never reconstructed from rate registers."""

    minimum_charge_power: float = 0
    maximum_charge_power: float = 0
    minimum_discharge_power: float = 0
    maximum_discharge_power: float = 0


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


def power_window(settings: PowerSettings, reference: float, mode: str):
    """Build boundaries using the signed power convention documented in README.

    The interval is [-InWRte, OutWRte]. A positive minimum excludes the
    opposite direction; both positive minima cannot describe one interval.
    """
    if mode == "automatic":
        return {"StorCtl_Mod": 0, "InWRte": 100, "OutWRte": 100}
    values = {
        key: watts_to_percent(value, reference)
        for key, value in asdict(settings).items()
    }
    for direction in ("charge", "discharge"):
        if values[f"minimum_{direction}_power"] > values[f"maximum_{direction}_power"]:
            raise ServiceValidationError("minimum power exceeds maximum power")
    if settings.minimum_charge_power and settings.minimum_discharge_power:
        raise ServiceValidationError(
            "charging and discharging minima cannot both be positive"
        )
    if mode != "manual":
        raise ServiceValidationError("unknown storage operating mode")
    return {
        "StorCtl_Mod": 3,
        "InWRte": (
            -values["minimum_discharge_power"]
            if settings.minimum_discharge_power
            else values["maximum_charge_power"]
        ),
        "OutWRte": (
            -values["minimum_charge_power"]
            if settings.minimum_charge_power
            else values["maximum_discharge_power"]
        ),
    }


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
            device: mode
            for device, mode in saved.get("modes", {}).items()
            if mode in ("automatic", "manual")
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

    def values(self, device_id):
        saved = self.settings.get(str(device_id))
        if saved is not None:
            return PowerSettings(**saved)
        reference = self.reference(device_id)
        return PowerSettings(0, reference, 0, reference)

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
            settings = self.values(device_id)
            if field is not None:
                settings = replace(settings, **{field: value})
            selected_mode = mode if mode is not None else self.mode(device_id)
            # Automatic release is independent of saved power constraints.
            if field is not None or selected_mode != "automatic":
                target = power_window(settings, self.reference(device_id), "manual")
            else:
                target = power_window(settings, 0, "automatic")
            if selected_mode not in ("automatic", "manual"):
                raise ServiceValidationError("unknown storage operating mode")
            applied = mode is not None or selected_mode == "manual"
            if applied:
                # Disable limits, broaden both boundaries, narrow them, then enable.
                # Every intermediate interval is valid, but this is not atomic Modbus.
                sequence = [
                    (124, "StorCtl_Mod", 0),
                    (124, "InWRte", 100),
                    (124, "OutWRte", 100),
                ]
                if selected_mode == "manual":
                    sequence.extend(
                        [
                            (124, "InWRte", target["InWRte"]),
                            (124, "OutWRte", target["OutWRte"]),
                            (124, "StorCtl_Mod", 3),
                        ]
                    )
                await self.coordinator.write_runtime.async_write_sequence(
                    device_id, sequence
                )
            updated = {**self.settings, str(device_id): asdict(settings)}
            modes = {**self.modes, str(device_id): selected_mode}
            targets = dict(self.last_targets)
            if applied:
                targets[str(device_id)] = target
            await self.store.async_save(
                {
                    "settings": updated,
                    "modes": modes,
                    "last_targets": targets,
                }
            )
            self.settings = updated
            self.modes = modes
            self.last_targets = targets
            self.coordinator.async_update_listeners()
