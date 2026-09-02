"""Shared fixed-register source helpers for writable entity platforms."""

from dataclasses import dataclass

from .coordinator import FroniusPVCoordinator
from .entity_naming import suggested_object_id
from .models import (
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    RegisterDefinition,
)
from .sensor import DeviceMetadata, _device_metadata
from .write_policy import WritePolicy


@dataclass(frozen=True, slots=True)
class ControlEntitySource:
    """Stable coordinates and policy for one fixed writable entity."""

    device_id: int
    model_id: int
    model_occurrence: int
    register_name: str
    register: RegisterDefinition
    entity: EntityDefinition
    role: PhysicalDeviceRole
    translation_key: str
    policy: WritePolicy | None
    device_metadata: DeviceMetadata | None = None
    block_name: None = None
    instance_index: None = None


def control_entity_sources(
    coordinator: FroniusPVCoordinator, platform: EntityPlatform
) -> tuple[ControlEntitySource, ...]:
    """Build fixed sources from discovered catalog metadata."""
    sources = []
    for device in coordinator.data.devices:
        metadata = _device_metadata(device.decoded_models)
        occurrences: dict[int, int] = {}
        for snapshot in device.decoded_models:
            model_id = snapshot.discovered.model_id
            occurrence = occurrences.get(model_id, 0)
            occurrences[model_id] = occurrence + 1
            for register in snapshot.definition.registers:
                entity = register.entity
                policy = coordinator.write_policies.get((model_id, register.name))
                if (
                    entity is None
                    or entity.platform is not platform
                    or entity.device_role is None
                    or entity.translation_key is None
                ):
                    continue
                sources.append(
                    ControlEntitySource(
                        device_id=device.device_id,
                        model_id=model_id,
                        model_occurrence=occurrence,
                        register_name=register.name,
                        register=register,
                        entity=entity,
                        role=entity.device_role,
                        translation_key=entity.translation_key,
                        policy=policy,
                        device_metadata=metadata,
                    )
                )
    return tuple(sources)


def current_control_value(
    coordinator: FroniusPVCoordinator, source: ControlEntitySource
):
    """Return the latest confirmed decoded value for one fixed source."""
    device = next(
        (
            device
            for device in coordinator.data.devices
            if device.device_id == source.device_id
        ),
        None,
    )
    if device is None:
        return None
    matching = [
        snapshot
        for snapshot in device.decoded_models
        if snapshot.discovered.model_id == source.model_id
    ]
    if source.model_occurrence >= len(matching):
        return None
    value = matching[source.model_occurrence].decoded.fixed.get(source.register_name)
    return None if value is None else value.value


def current_control_raw_value(
    coordinator: FroniusPVCoordinator, source: ControlEntitySource
):
    """Return the latest confirmed raw value for one fixed source."""
    device = next(
        (
            item
            for item in coordinator.data.devices
            if item.device_id == source.device_id
        ),
        None,
    )
    if device is None:
        return None
    matching = [
        snapshot
        for snapshot in device.decoded_models
        if snapshot.discovered.model_id == source.model_id
    ]
    if source.model_occurrence >= len(matching):
        return None
    value = matching[source.model_occurrence].decoded.fixed.get(source.register_name)
    return None if value is None else value.raw


def suggested_control_object_id(source: ControlEntitySource) -> str:
    """Return one stable language-independent low-level object ID."""
    return suggested_object_id(source.entity)
