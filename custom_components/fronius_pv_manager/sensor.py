"""Read-only sensor entities backed exclusively by coordinator data."""

from dataclasses import dataclass

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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FroniusPVConfigEntry
from .const import DOMAIN
from .coordinator import FroniusPVCoordinator
from .models import (
    EntityCategoryHint,
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    RegisterDataType,
    RegisterDefinition,
)
from .semantics import classify_model_160_module

_DEVICE_NAMES = {
    PhysicalDeviceRole.INVERTER: "Fronius Inverter",
    PhysicalDeviceRole.STORAGE: "Fronius Storage",
    PhysicalDeviceRole.METER: "Fronius Smart Meter",
}

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
}


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
    block_name: str | None = None
    instance_index: int | None = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroniusPVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create catalog-backed sensors from the first coordinator snapshot."""
    coordinator = entry.runtime_data
    sources = _sensor_sources(coordinator)
    devices_by_role: dict[PhysicalDeviceRole, set[int]] = {}
    for source in sources:
        devices_by_role.setdefault(source.role, set()).add(source.device_id)
    async_add_entities(
        FroniusPVSensor(
            coordinator,
            entry.entry_id,
            source,
            distinguish_device_name=len(devices_by_role[source.role]) > 1,
        )
        for source in sources
    )


def _sensor_sources(coordinator: FroniusPVCoordinator) -> tuple[SensorSource, ...]:
    """Build stable source descriptors without retaining decoded values."""
    sources = []
    for device in coordinator.data.devices:
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
                    )
                )
            for block in snapshot.definition.repeating_blocks:
                definitions = {
                    register.name: register for register in block.registers
                }
                for instance in snapshot.decoded.repeating.get(block.name, ()):
                    classified = classify_model_160_module(instance)
                    if classified.physical_role is None:
                        continue
                    for register in definitions.values():
                        entity = register.entity
                        if (
                            entity is None
                            or entity.platform is not EntityPlatform.SENSOR
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
                                block_name=block.name,
                                instance_index=instance.instance_index,
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
        self.entity_description = _entity_description(source.register, source.entity)
        identity = [
            entry_id,
            f"device{source.device_id}",
            source.role.value,
            source.entity.key,
        ]
        if source.block_name is not None:
            identity.extend((source.block_name, f"instance{source.instance_index}"))
        self._attr_unique_id = "_".join(identity)
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{entry_id}:device{source.device_id}:{source.role.value}",
                )
            },
            manufacturer="Fronius",
            name=(
                f"{_DEVICE_NAMES[source.role]} {source.device_id}"
                if distinguish_device_name
                else _DEVICE_NAMES[source.role]
            ),
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
        return super().available and device is not None and device.available

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
        return value.value if value is not None else None


def _entity_description(
    register: RegisterDefinition, entity: EntityDefinition
) -> SensorEntityDescription:
    """Map neutral catalog metadata to conservative Home Assistant metadata."""
    unit, device_class, state_class = _sensor_metadata(register, entity)
    category = {
        EntityCategoryHint.DIAGNOSTIC: EntityCategory.DIAGNOSTIC,
        EntityCategoryHint.CONFIG: EntityCategory.CONFIG,
    }.get(entity.category)
    return SensorEntityDescription(
        key=entity.key,
        name=register.description or register.name,
        device_class=device_class,
        state_class=state_class,
        native_unit_of_measurement=unit,
        entity_category=category,
        entity_registry_enabled_default=entity.enabled_by_default,
    )


def _sensor_metadata(
    register: RegisterDefinition, entity: EntityDefinition
) -> tuple[str | None, SensorDeviceClass | None, SensorStateClass | None]:
    """Return canonical units/classes without altering decoded values."""
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
        return register.unit, device_class, state_class
    if register.unit == "Wh" and register.data_type in {
        RegisterDataType.ACC32,
        RegisterDataType.ACC64,
    }:
        return (
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        )
    return _UNIT_METADATA.get(register.unit, (register.unit, None, None))
