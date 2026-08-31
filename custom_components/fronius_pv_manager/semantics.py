"""Infer device capabilities and classify decoded SunSpec model semantics."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .model_decoder import DecodedModel, DecodedRepeatingBlockInstance
from .models import (
    Capability,
    DeviceProfile,
    DiscoveredModel,
    PhysicalDeviceRole,
    RegisterAccess,
)
from .register_maps import MODEL_124


class Model160ModuleKind(StrEnum):
    """Semantic meanings currently recognized for Model 160 modules."""

    MPPT = "mppt"
    STORAGE_CHARGE = "storage_charge"
    STORAGE_DISCHARGE = "storage_discharge"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClassifiedModel160Module:
    """Immutable semantic classification of one zero-based Model 160 instance."""

    instance_index: int
    module_id: int | None
    module_name: str | None
    physical_role: PhysicalDeviceRole | None
    semantic_kind: Model160ModuleKind


def physical_role_for_model(model_id: int) -> PhysicalDeviceRole | None:
    """Return the physical role for a whole-model semantic assignment."""
    if model_id in {103, 120, 121, 122, 123}:
        return PhysicalDeviceRole.INVERTER
    if model_id == 124:
        return PhysicalDeviceRole.STORAGE
    if model_id == 203:
        return PhysicalDeviceRole.METER
    return None


def infer_device_profile(models: Iterable[DiscoveredModel]) -> DeviceProfile:
    """Build a static capability profile solely from discovered model metadata."""
    discovered = tuple(models)
    model_ids = {model.model_id for model in discovered}
    capabilities: set[Capability] = set()

    if 103 in model_ids:
        capabilities.add(Capability.INVERTER)
    if 203 in model_ids:
        capabilities.add(Capability.METER)
    if 123 in model_ids:
        capabilities.add(Capability.CONTROLS)
    if 124 in model_ids:
        capabilities.add(Capability.STORAGE)
        writable_names = {
            register.name
            for register in MODEL_124.registers
            if register.access is RegisterAccess.READ_WRITE
        }
        if {"StorCtl_Mod", "OutWRte", "InWRte"} <= writable_names:
            capabilities.add(Capability.STORAGE_CONTROL)
        if "ChaGriSet" in writable_names:
            capabilities.add(Capability.GRID_CHARGE_CONTROL)

    return DeviceProfile(discovered, frozenset(capabilities))


def classify_model_160_module(
    instance: DecodedRepeatingBlockInstance,
) -> ClassifiedModel160Module:
    """Classify one decoded Model 160 module from its ID and IDStr values."""
    id_value = instance.values.get("ID")
    module_id = (
        id_value.value
        if id_value is not None and type(id_value.value) is int
        else None
    )
    name_value = instance.values.get("IDStr")
    module_name = (
        name_value.value.strip()
        if name_value is not None and isinstance(name_value.value, str)
        else None
    )
    if not module_name:
        module_name = None
        role = None
        kind = Model160ModuleKind.UNKNOWN
    elif module_name.startswith("MPPT"):
        role = PhysicalDeviceRole.INVERTER
        kind = Model160ModuleKind.MPPT
    elif module_name.startswith("StDisCha"):
        role = PhysicalDeviceRole.STORAGE
        kind = Model160ModuleKind.STORAGE_DISCHARGE
    elif module_name.startswith("StCha"):
        role = PhysicalDeviceRole.STORAGE
        kind = Model160ModuleKind.STORAGE_CHARGE
    else:
        role = None
        kind = Model160ModuleKind.UNKNOWN

    return ClassifiedModel160Module(
        instance_index=instance.instance_index,
        module_id=module_id,
        module_name=module_name,
        physical_role=role,
        semantic_kind=kind,
    )


def classify_model_160_modules(
    decoded: DecodedModel,
) -> tuple[ClassifiedModel160Module, ...]:
    """Classify all decoded instances of the neutral Model 160 module block."""
    return tuple(
        classify_model_160_module(instance)
        for instance in decoded.repeating.get("module", ())
    )


def augment_profile_with_model_160(
    profile: DeviceProfile, decoded: DecodedModel
) -> DeviceProfile:
    """Return a new profile with MPPT added only for an observed MPPT module."""
    capabilities = set(profile.capabilities)
    if any(
        module.semantic_kind is Model160ModuleKind.MPPT
        for module in classify_model_160_modules(decoded)
    ):
        capabilities.add(Capability.MPPT)
    return DeviceProfile(profile.models, frozenset(capabilities))
