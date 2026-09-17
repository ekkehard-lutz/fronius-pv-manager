"""Read-only sensor entities backed exclusively by coordinator data."""

import math
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FroniusPVConfigEntry
from .const import DOMAIN
from .coordinator import FroniusPVCoordinator
from .entity_naming import suggested_object_id
from .models import (
    EntityCategoryHint,
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    RegisterDataType,
    RegisterDefinition,
)
from .semantics import Model160ModuleKind, classify_model_160_module
from .solar_entity import SolarEntity, setup_solar_entities

_UNIT_METADATA = {
    "W": (UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "V": (
        UnitOfElectricPotential.VOLT,
        SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT,
    ),
    "A": (
        UnitOfElectricCurrent.AMPERE,
        SensorDeviceClass.CURRENT,
        SensorStateClass.MEASUREMENT,
    ),
    "Hz": (
        UnitOfFrequency.HERTZ,
        SensorDeviceClass.FREQUENCY,
        SensorStateClass.MEASUREMENT,
    ),
    "C": (
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
    ),
    "Pct": (
        UnitOfRatio.PERCENTAGE,
        SensorDeviceClass.POWER_FACTOR,
        SensorStateClass.MEASUREMENT,
    ),
    "cos()": (None, SensorDeviceClass.POWER_FACTOR, SensorStateClass.MEASUREMENT),
    "ohms": ("Ω", None, SensorStateClass.MEASUREMENT),
    "Secs": (UnitOfTime.SECONDS, None, None),
    "% WChaMax/sec": ("% WChaMax/s", None, None),
}


@dataclass(frozen=True, slots=True)
class DeviceMetadata:
    """Optional physical identity decoded from Common Model 1."""

    manufacturer: str | None
    model: str | None
    serial_number: str | None
    sw_version: str | None


@dataclass(frozen=True, slots=True)
class SensorSource:
    """Immutable coordinates for a value in the latest coordinator snapshot."""

    device_id: int
    model_id: int
    model_occurrence: int
    register_name: str
    register: RegisterDefinition
    entity: EntityDefinition
    role: PhysicalDeviceRole
    translation_key: str
    translation_placeholders: dict[str, str] | None = None
    device_metadata: DeviceMetadata | None = None
    block_name: str | None = None
    instance_index: int | None = None
    model_160_kind: Model160ModuleKind | None = None
    mppt_number: int | None = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroniusPVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create cached sensors and add newly discovered sources during polling."""
    # Import after sensor metadata helpers are defined: storage discovery shares them.
    from .storage_status import setup_storage_status

    setup_storage_status(entry, async_add_entities)
    setup_solar_entities(entry, async_add_entities, _solar_sensors)
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_entities():
        sources = _sensor_sources(coordinator)
        devices_by_role: dict[PhysicalDeviceRole, set[int]] = {}
        for source in sources:
            devices_by_role.setdefault(source.role, set()).add(source.device_id)
        entities = list(
            FroniusPVSensor(
                coordinator,
                entry.entry_id,
                source,
                distinguish_device_name=len(devices_by_role[source.role]) > 1,
            )
            for source in sources
        )
        fresh = [entity for entity in entities if entity.unique_id not in known]
        known.update(entity.unique_id for entity in fresh)
        if fresh:
            async_add_entities(fresh)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


def _sensor_sources(coordinator: FroniusPVCoordinator) -> tuple[SensorSource, ...]:
    """Build stable source descriptors without retaining decoded values."""
    sources = []
    for device in coordinator.entity_data.devices:
        device_metadata = _device_metadata(device.decoded_models)
        occurrences: dict[int, int] = {}
        for snapshot in device.decoded_models:
            model_id = snapshot.discovered.model_id
            occurrence = occurrences.get(model_id, 0)
            occurrences[model_id] = occurrence + 1
            for register in snapshot.definition.registers:
                entity = register.entity
                if (
                    entity is None
                    or entity.platform is not EntityPlatform.SENSOR
                    or entity.device_role is None
                    or entity.translation_key is None
                ):
                    continue
                sources.append(
                    SensorSource(
                        device_id=device.device_id,
                        model_id=model_id,
                        model_occurrence=occurrence,
                        register_name=register.name,
                        register=register,
                        entity=entity,
                        role=entity.device_role,
                        translation_key=entity.translation_key,
                        device_metadata=device_metadata,
                    )
                )
            for block in snapshot.definition.repeating_blocks:
                definitions = {register.name: register for register in block.registers}
                mppt_number = 0
                for instance in snapshot.decoded.repeating.get(block.name, ()):
                    classified = classify_model_160_module(instance)
                    if classified.physical_role is None:
                        continue
                    if classified.semantic_kind is Model160ModuleKind.MPPT:
                        mppt_number += 1
                    for register in definitions.values():
                        entity = register.entity
                        if (
                            entity is None
                            or entity.platform is not EntityPlatform.SENSOR
                            or entity.translation_key is None
                        ):
                            continue
                        sources.append(
                            SensorSource(
                                device_id=device.device_id,
                                model_id=model_id,
                                model_occurrence=occurrence,
                                register_name=register.name,
                                register=register,
                                entity=entity,
                                role=classified.physical_role,
                                translation_key=_model_160_translation_key(
                                    classified.semantic_kind,
                                    register.name,
                                ),
                                translation_placeholders=(
                                    {"number": str(mppt_number)}
                                    if classified.semantic_kind
                                    is Model160ModuleKind.MPPT
                                    else None
                                ),
                                device_metadata=device_metadata,
                                block_name=block.name,
                                instance_index=instance.instance_index,
                                model_160_kind=classified.semantic_kind,
                                mppt_number=(
                                    mppt_number
                                    if classified.semantic_kind
                                    is Model160ModuleKind.MPPT
                                    else None
                                ),
                            )
                        )
    return tuple(sources)


class FroniusPVSensor(CoordinatorEntity[FroniusPVCoordinator], SensorEntity):
    """Expose one decoded catalog value from the latest coordinator data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FroniusPVCoordinator,
        entry_id: str,
        source: SensorSource,
        *,
        distinguish_device_name: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._source = source
        self.entity_description = _entity_description(
            source.register,
            source.entity,
            source.translation_key,
            source.translation_placeholders,
        )
        identity = [
            entry_id,
            f"device{source.device_id}",
            source.role.value,
            source.entity.key,
        ]
        if source.block_name is not None:
            identity.extend((source.block_name, f"instance{source.instance_index}"))
        self._attr_unique_id = "_".join(identity)
        self._attr_device_info = _device_info(
            entry_id,
            source,
            distinguish_device_name,
        )

    @property
    def suggested_object_id(self) -> str:
        """Return a stable semantic ID independent of translated display names."""
        return suggested_object_id(
            self._source.entity,
            model_160_kind=self._source.model_160_kind,
            register_name=self._source.register_name,
            mppt_number=self._source.mppt_number,
        )

    @property
    def available(self) -> bool:
        """Combine endpoint refresh status with this device's poll status."""
        device = next(
            (
                device
                for device in self.coordinator.data.devices
                if device.device_id == self._source.device_id
            ),
            None,
        )
        matching = (
            []
            if device is None
            else [
                model
                for model in device.decoded_models
                if model.discovered.model_id == self._source.model_id
            ]
        )
        return (
            super().available
            and device is not None
            and device.available
            and self._source.model_occurrence < len(matching)
            and matching[self._source.model_occurrence].available
        )

    @property
    def native_value(self):
        """Read the current decoded semantic value from coordinator data."""
        device = next(
            (
                device
                for device in self.coordinator.data.devices
                if device.device_id == self._source.device_id
            ),
            None,
        )
        if device is None:
            return None
        matching = [
            snapshot
            for snapshot in device.decoded_models
            if snapshot.discovered.model_id == self._source.model_id
        ]
        if self._source.model_occurrence >= len(matching):
            return None
        decoded = matching[self._source.model_occurrence].decoded
        if self._source.block_name is None:
            value = decoded.fixed.get(self._source.register_name)
        else:
            instances = decoded.repeating.get(self._source.block_name, ())
            index = self._source.instance_index
            if index is None or index >= len(instances):
                return None
            value = instances[index].values.get(self._source.register_name)
        if value is None:
            return None
        if self._source.entity.translate_enum_values:
            if not isinstance(value.value, str):
                return None
            option = _enum_option(value.value)
            options = self.entity_description.options
            return option if options is not None and option in options else None
        return value.value


def _entity_description(
    register: RegisterDefinition,
    entity: EntityDefinition,
    translation_key: str,
    translation_placeholders: dict[str, str] | None,
) -> SensorEntityDescription:
    """Map neutral catalog metadata to conservative Home Assistant metadata."""
    unit, device_class, state_class = _sensor_metadata(register, entity)
    options = None
    if entity.translate_enum_values:
        device_class = SensorDeviceClass.ENUM
        state_class = None
        options = (
            [_enum_option(label) for label in register.enum.values()]
            if register.enum is not None
            else []
        )
    category = (
        EntityCategory.DIAGNOSTIC
        if entity.category is EntityCategoryHint.DIAGNOSTIC
        else None
    )
    return SensorEntityDescription(
        key=entity.key,
        translation_key=translation_key,
        translation_placeholders=translation_placeholders,
        device_class=device_class,
        state_class=state_class,
        native_unit_of_measurement=unit,
        options=options,
        entity_category=category,
        entity_registry_enabled_default=entity.enabled_by_default,
    )


def _sensor_metadata(
    register: RegisterDefinition, entity: EntityDefinition
) -> tuple[str | None, SensorDeviceClass | None, SensorStateClass | None]:
    """Return canonical units/classes without altering decoded values."""
    unit = entity.presentation_unit or register.unit
    if entity.device_class is not None or entity.state_class is not None:
        device_class = (
            SensorDeviceClass(entity.device_class)
            if entity.device_class is not None
            else None
        )
        state_class = (
            SensorStateClass(entity.state_class)
            if entity.state_class is not None
            else None
        )
        return unit, device_class, state_class
    if unit == "Wh" and register.data_type in {
        RegisterDataType.ACC32,
        RegisterDataType.ACC64,
    }:
        return (
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        )
    return _UNIT_METADATA.get(unit, (unit, None, None))


def _clean_metadata_value(value: Any) -> str | None:
    """Return one non-empty decoded metadata string."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _device_metadata(
    snapshots: tuple,
) -> DeviceMetadata | None:
    """Read optional identity solely from an already-decoded Common Model 1."""
    common = next(
        (snapshot for snapshot in snapshots if snapshot.discovered.model_id == 1),
        None,
    )
    if common is None or not common.decoded.fixed:
        return None
    fixed = common.decoded.fixed
    return DeviceMetadata(
        manufacturer=_clean_metadata_value(fixed["Mn"].value),
        model=_clean_metadata_value(fixed["Md"].value),
        serial_number=_clean_metadata_value(fixed["SN"].value),
        sw_version=_clean_metadata_value(fixed["Vr"].value),
    )


def _device_info(
    entry_id: str,
    source: SensorSource,
    distinguish_name: bool,
) -> DeviceInfo:
    """Build localized fallback or decoded physical device presentation."""
    identifiers = {(DOMAIN, f"{entry_id}:device{source.device_id}:{source.role.value}")}
    metadata = (
        source.device_metadata
        if source.role is not PhysicalDeviceRole.STORAGE
        else None
    )
    if metadata is not None and metadata.model is not None:
        return DeviceInfo(
            identifiers=identifiers,
            manufacturer=metadata.manufacturer,
            model=metadata.model,
            serial_number=metadata.serial_number,
            sw_version=metadata.sw_version,
            name=metadata.model,
        )
    translation_key = f"{source.role.value}_device"
    placeholders = None
    if distinguish_name:
        translation_key += "_with_id"
        placeholders = {"device_id": str(source.device_id)}
    info: dict[str, Any] = {
        "identifiers": identifiers,
        "translation_key": translation_key,
        "translation_placeholders": placeholders,
    }
    if metadata is not None:
        if metadata.manufacturer is not None:
            info["manufacturer"] = metadata.manufacturer
        if metadata.serial_number is not None:
            info["serial_number"] = metadata.serial_number
        if metadata.sw_version is not None:
            info["sw_version"] = metadata.sw_version
    return DeviceInfo(**info)


def _model_160_translation_key(kind: Model160ModuleKind, register_name: str) -> str:
    """Select presentation semantics from the existing module classifier."""
    semantic = {
        Model160ModuleKind.MPPT: "mppt",
        Model160ModuleKind.STORAGE_CHARGE: "storage_charging",
        Model160ModuleKind.STORAGE_DISCHARGE: "storage_discharging",
    }[kind]
    return f"model_160_{semantic}_{register_name.lower()}"


def _enum_option(label: str) -> str:
    """Normalize one decoded enum label to a stable HA option identifier."""
    label = label.replace("%", " percent ")
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


class BatteryOperationMode(SolarEntity, SensorEntity):
    """Unrestricted raw strings allow future firmware states without enum rejection."""

    @property
    def native_value(self):
        return self.value


class SolarPowerSensor(SolarEntity, SensorEntity):
    """Read semantic power from current Modbus data, independent of Solar API."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def value(self):
        if not self.coordinator.last_update_success:
            return None
        device = next(
            (d for d in self.coordinator.data.devices if d.device_id == self.device_id),
            None,
        )
        if device is None or not device.available:
            return None
        if self.key == "pv_power":
            powers = []
            for snapshot in device.decoded_models:
                if snapshot.discovered.model_id != 160 or not snapshot.available:
                    continue
                for module in snapshot.decoded.repeating.get("module", ()):
                    if (
                        classify_model_160_module(module).semantic_kind
                        is Model160ModuleKind.MPPT
                    ):
                        power = module.values.get("DCW")
                        if power is not None and power.value is not None:
                            powers.append(power.value)
            return sum(powers) if powers else None
        meter = next(
            (s for s in device.decoded_models if s.discovered.model_id == 203),
            None,
        )
        if meter is None or not meter.available:
            return None
        power = meter.decoded.fixed.get("W")
        if power is None or power.value is None:
            return None
        # Fronius Model 203 at the grid connection: positive import, negative export.
        # https://manuals.fronius.com/html/4204102649/en-US.html (Meter Model)
        return _grid_direction_power(power.value, self.key)

    @property
    def native_value(self):
        return self.value


def _grid_direction_power(power, key):
    """Share the existing signed-meter semantics with site calculations."""
    return max(power if key == "grid_import_power" else -power, 0)


def _active_power(coordinator, device_id, model_id):
    """Read one finite active-power value without using cached offline readings."""
    if not coordinator.last_update_success:
        return None
    device = next(
        (d for d in coordinator.data.devices if d.device_id == device_id), None
    )
    if device is None or not device.available:
        return None
    models = [s for s in device.decoded_models if s.discovered.model_id == model_id]
    if len(models) != 1 or not models[0].available:
        return None
    power = models[0].decoded.fixed.get("W")
    value = power.value if power is not None else None
    return value if type(value) in (int, float) and math.isfinite(value) else None


class SolarConsumptionSensor(SolarEntity, SensorEntity):
    """Site consumption and instantaneous ratios on the existing inverter."""

    def __init__(self, coordinator, entry_id, device_id, key):
        super().__init__(coordinator, entry_id, device_id, key, "inverter")
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if key == "consumption_power":
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_device_class = SensorDeviceClass.POWER

    @property
    def value(self):
        # Topology, rather than just online devices, prevents silently switching
        # sources when a meter or inverter goes offline. No site mapping exists.
        topology = self.coordinator.entity_data.devices
        inverters = [
            d.device_id
            for d in topology
            if any(m.model_id == 103 for m in d.discovered_models)
        ]
        meters = [
            d.device_id
            for d in topology
            if any(m.model_id == 203 for m in d.discovered_models)
        ]
        if inverters != [self.device_id] or len(meters) != 1:
            return None
        ac_power = _active_power(self.coordinator, self.device_id, 103)
        meter_power = _active_power(self.coordinator, meters[0], 203)
        if ac_power is None or meter_power is None:
            return None
        imported = _grid_direction_power(meter_power, "grid_import_power")
        exported = _grid_direction_power(meter_power, "grid_export_power")
        consumption = max(0, ac_power + imported - exported)
        if not math.isfinite(consumption):
            return None
        if self.key == "consumption_power":
            return consumption
        if self.key == "autarky":
            if consumption <= 0:
                return None
            ratio = 100 * (1 - imported / consumption)
        else:
            if ac_power <= 0:
                return 100.0 if exported == 0 else None
            ratio = 100 * consumption / ac_power
        return min(100, max(0, ratio))

    @property
    def native_value(self):
        return self.value


def _efficiency_number(values, key):
    """Read a finite, nonnegative decoded engineering value."""
    register = values.get(key)
    value = register.value if register is not None else None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value


class SolarEfficiencySensor(SolarEntity, SensorEntity):
    """Efficiency from unambiguous models on one Modbus inverter/storage system."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def value(self):
        if not self.coordinator.last_update_success:
            return None
        device = next(
            (d for d in self.coordinator.data.devices if d.device_id == self.device_id),
            None,
        )
        if device is None or not device.available:
            return None
        rectifier = self.key == "rectifier_efficiency"
        if (
            rectifier
            and sum(
                d.device_id == self.device_id for d in self.coordinator.data.devices
            )
            != 1
        ):
            return None
        inverter = self.key == "inverter_efficiency" or rectifier
        required = (103, 160) if inverter else (120, 124, 160)
        models = {}
        for model_id in required:
            matches = [
                s for s in device.decoded_models if s.discovered.model_id == model_id
            ]
            discovered = [m for m in device.discovered_models if m.model_id == model_id]
            if len(discovered) != 1 or len(matches) != 1 or not matches[0].available:
                return None
            models[model_id] = matches[0].decoded
        modules = {kind: [] for kind in Model160ModuleKind}
        for module in models[160].repeating.get("module", ()):
            modules[classify_model_160_module(module).semantic_kind].append(module)
        charge = modules[Model160ModuleKind.STORAGE_CHARGE]
        discharge = modules[Model160ModuleKind.STORAGE_DISCHARGE]
        if len(charge) != 1 or len(discharge) != 1:
            return None
        field = "DCW" if inverter else "DCWH"
        charging = _efficiency_number(charge[0].values, field)
        discharging = _efficiency_number(discharge[0].values, field)
        if charging is None or discharging is None:
            return None
        if inverter:
            # Decoder enum label from Model 103 St=4, never an HA translation.
            state = models[103].fixed.get("St")
            if state is None or state.value != "MPPT":
                return None
            if rectifier:
                register = models[103].fixed.get("W")
                ac = register.value if register is not None else None
                if type(ac) not in (int, float) or not math.isfinite(ac) or ac >= 0:
                    return None
            else:
                ac = _efficiency_number(models[103].fixed, "W")
            pv = [
                _efficiency_number(m.values, "DCW")
                for m in modules[Model160ModuleKind.MPPT]
            ]
            if ac is None or not pv or any(p is None for p in pv):
                return None
            denominator = sum(pv) + discharging - charging
            numerator = ac
            if rectifier:
                if denominator >= 0 or not math.isfinite(denominator):
                    return None
                numerator, denominator = -denominator, -ac
        else:
            soc = _efficiency_number(models[124].fixed, "ChaState")
            capacity = _efficiency_number(models[120].fixed, "WHRtg")
            if soc is None or soc > 100 or capacity is None or capacity <= 0:
                return None
            # DCWH and WHRtg are already scaled Wh; ChaState is percent.
            numerator = discharging + capacity * (soc / 100)
            denominator = charging
        if denominator <= 0 or not math.isfinite(denominator):
            return None
        result = 100 * (numerator / denominator)
        return result if math.isfinite(result) else None

    @property
    def native_value(self):
        return self.value


def _solar_sensors(coordinator, entry_id, device_id):
    return [
        SolarEfficiencySensor(
            coordinator, entry_id, device_id, "inverter_efficiency", "inverter"
        ),
        SolarEfficiencySensor(
            coordinator, entry_id, device_id, "rectifier_efficiency", "inverter"
        ),
        SolarEfficiencySensor(
            coordinator, entry_id, device_id, "battery_lifetime_efficiency", "storage"
        ),
        *(
            SolarConsumptionSensor(coordinator, entry_id, device_id, key)
            for key in ("consumption_power", "autarky", "self_consumption")
        ),
        BatteryOperationMode(
            coordinator, entry_id, device_id, "battery_operation_mode", "storage"
        ),
        SolarPowerSensor(coordinator, entry_id, device_id, "pv_power", "inverter"),
        SolarPowerSensor(
            coordinator, entry_id, device_id, "grid_import_power", "meter"
        ),
        SolarPowerSensor(
            coordinator, entry_id, device_id, "grid_export_power", "meter"
        ),
    ]
