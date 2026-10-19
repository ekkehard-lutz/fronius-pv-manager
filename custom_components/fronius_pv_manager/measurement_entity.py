"""Sensor-only retention and publication on existing coordinator callbacks."""

from homeassistant.core import callback

from .const import FAST_MODEL_IDS
from .measurement import EfficiencyWindow, normalize_measurement
from .solar_entity import SolarEntity


class MeasurementSolarEntity(SolarEntity):
    """Keep a complete derived reading across partial failed Modbus cycles.

    Formula properties continue to consume live, unrounded data. Only this
    publication layer may retain a result; control code never reads this cache.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._managed = False
        self._published_value = None
        self._last_sources = None
        self._epochs = None
        self._calculated_sources = None
        self._calculated_value = None
        self._validated_communication_revisions = None
        self._window = EfficiencyWindow() if self.key.endswith("efficiency") else None

    @property
    def published_value(self):
        if self._managed:
            return self._published_value
        # An unattached entity has no history. HA attaches it before publication.
        value = self.value
        if self._window:
            window = EfficiencyWindow()
            window.observe(0, value, None)
            return window.publish()
        return normalize_measurement(value, self.native_unit_of_measurement)

    @property
    def available(self):
        return (
            self.published_value is not None
            and all(
                self.coordinator.measurement_available(unit)
                for unit, _ in self._dependencies()
            )
            and (
                not self._managed
                or self._validated_communication_revisions
                == self._communication_revisions()
            )
        )

    def _communication_revisions(self):
        return tuple(
            sorted(
                (unit, self.coordinator.communication_timeout_revisions.get(unit, 0))
                for unit in {unit for unit, _ in self._dependencies()}
            )
        )

    def _dependencies(self):
        if self.key == "pv_power":
            return ((self.device_id, 160),)
        if self.key in ("grid_import_power", "grid_export_power"):
            return ((self.device_id, 203),)
        if self.key in ("inverter_efficiency", "rectifier_efficiency"):
            return ((self.device_id, 103), (self.device_id, 160))
        if self.key == "battery_lifetime_efficiency":
            return tuple((self.device_id, mid) for mid in (120, 124, 160))
        return tuple(
            (device.device_id, model.model_id)
            for device in self.coordinator.entity_data.devices
            for model in device.discovered_models
            if model.model_id in (103, 203)
        )

    def _sources(self, dependencies):
        sources = []
        for device_id, model_id in dependencies:
            devices = [
                d for d in self.coordinator.data.devices if d.device_id == device_id
            ]
            if len(devices) != 1 or not devices[0].available:
                return None
            models = [
                s
                for s in devices[0].decoded_models
                if s.discovered.model_id == model_id
            ]
            if len(models) != 1 or not models[0].available:
                return None
            retained = [
                s
                for d in self.coordinator.measurement_data.devices
                if d.device_id == device_id
                for s in d.decoded_models
                if s.discovered == models[0].discovered and s.available
            ]
            if not retained:
                return None
            sources.append((device_id, model_id, models[0]))
        return sources or None

    def _mode(self):
        if self.key == "battery_lifetime_efficiency":
            # Lifetime counters are compatible across charge/discharge modes.
            for d in self.coordinator.data.devices:
                if d.device_id == self.device_id:
                    for s in d.decoded_models:
                        if s.available and s.discovered.model_id == 120:
                            return (s.decoded.fixed.get("WHRtg"),)
            return None
        for d in self.coordinator.data.devices:
            if d.device_id != self.device_id:
                continue
            for s in d.decoded_models:
                if s.available and s.discovered.model_id == 103:
                    state = s.decoded.fixed.get("St")
                    power = s.decoded.fixed.get("W")
                    if state is not None and power is not None:
                        value = power.value
                        if type(value) in (int, float) and value == value:
                            return (
                                state.value,
                                "rectifier" if value < 0 else "inverter",
                            )
        return None

    def _capture(self):
        self._managed = True
        dependencies = self._dependencies()
        epochs = tuple(
            (device, self.coordinator.measurement_epochs.get(device, 0))
            for device, _ in dependencies
        )
        if self._epochs is not None and epochs != self._epochs:
            self._published_value = None
            self._last_sources = None
            self._calculated_sources = None
            if self._window:
                self._window = EfficiencyWindow()
        self._epochs = epochs
        sources = self._sources(dependencies)
        fresh = sources is not None and self.coordinator.last_update_success
        if fresh and self._last_sources is not None:
            # Every FAST dependency must have a new complete model payload.
            fresh = all(
                model_id not in FAST_MODEL_IDS
                or all(snapshot is not old for _, _, old in self._last_sources)
                for _, model_id, snapshot in sources
            )
        value = None
        if fresh:
            unchanged = (
                self._calculated_sources is not None
                and len(sources) == len(self._calculated_sources)
                and all(
                    device == old_device
                    and mid == old_mid
                    and snapshot.discovered == old.discovered
                    and snapshot.decoded == old.decoded
                    for (device, mid, snapshot), (old_device, old_mid, old) in zip(
                        sources, self._calculated_sources, strict=True
                    )
                )
            )
            value = self._calculated_value if unchanged else self.value
            self._calculated_sources = sources
            self._calculated_value = value
        if sources is not None and fresh:
            self._last_sources = sources
        if value is not None:
            self._validated_communication_revisions = self._communication_revisions()
        if self._window:
            self._window.observe(self.coordinator.measurement_time, value, self._mode())
            if (
                fresh
                and value is not None
                and self.device_id in self.coordinator.slow_publication_devices
            ):
                result = self._window.publish()
                if result is not None:
                    self._published_value = result
            return False  # Informational sensors do not need identical fresh reports.
        if value is not None:
            self._published_value = normalize_measurement(
                value, self.native_unit_of_measurement
            )
            return True
        return False

    async def async_added_to_hass(self):
        self._capture()
        self._reported_measurement_available = self.available
        await super().async_added_to_hass()

    @callback
    def _handle_coordinator_update(self):
        previous = self._published_value
        report = self._capture()
        available = self.available
        changed = available != getattr(
            self, "_reported_measurement_available", available
        )
        self._reported_measurement_available = available
        if report or previous != self._published_value or changed:
            self.async_write_ha_state()
