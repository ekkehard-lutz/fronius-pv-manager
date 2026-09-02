"""Policy-approved writable numeric register entities."""

import math
from decimal import Decimal

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FroniusPVConfigEntry
from .control_entity import (
    ControlEntitySource,
    control_entity_sources,
    current_control_value,
    suggested_control_object_id,
)
from .coordinator import FroniusPVCoordinator
from .models import EntityCategoryHint, EntityPlatform
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroniusPVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create safely representable catalog number entities."""
    coordinator = entry.runtime_data
    sources = [
        source
        for source in control_entity_sources(coordinator, EntityPlatform.NUMBER)
        if _has_finite_hard_range(source)
    ]
    devices_by_role = {}
    for source in sources:
        devices_by_role.setdefault(source.role, set()).add(source.device_id)
    async_add_entities(
        FroniusPVNumber(
            coordinator,
            entry.entry_id,
            source,
            distinguish_device_name=len(devices_by_role[source.role]) > 1,
        )
        for source in sources
    )


class FroniusPVNumber(CoordinatorEntity[FroniusPVCoordinator], NumberEntity):
    """Expose one cataloged numeric register without optimistic state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FroniusPVCoordinator,
        entry_id: str,
        source: ControlEntitySource,
        *,
        distinguish_device_name: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._source = source
        minimum, maximum = _effective_range(source)
        if minimum is None or maximum is None:
            raise ValueError("writable number requires finite effective bounds")
        self.entity_description = NumberEntityDescription(
            key=source.entity.key,
            translation_key=source.translation_key,
            native_min_value=float(minimum),
            native_max_value=float(maximum),
            native_step=_effective_step(source),
            native_unit_of_measurement=(
                source.entity.presentation_unit or source.register.unit
            ),
            entity_category=_entity_category(source.entity.category),
            entity_registry_enabled_default=source.entity.enabled_by_default,
        )
        self._attr_unique_id = "_".join(
            (
                entry_id,
                f"device{source.device_id}",
                source.role.value,
                source.entity.key,
            )
        )
        self._attr_device_info = _device_info(
            entry_id, source, distinguish_device_name
        )

    @property
    def available(self) -> bool:
        """Combine coordinator and device-specific availability."""
        device = next(
            (
                item
                for item in self.coordinator.data.devices
                if item.device_id == self._source.device_id
            ),
            None,
        )
        return super().available and device is not None and device.available

    @property
    def native_value(self) -> float | None:
        """Return only the latest coordinator-confirmed semantic value."""
        value = current_control_value(self.coordinator, self._source)
        return float(value) if type(value) in {int, float} else None

    @property
    def suggested_object_id(self) -> str:
        """Return a stable language-independent low-level object ID."""
        return suggested_control_object_id(self._source)

    async def async_set_native_value(self, value: float) -> None:
        """Write through policy, encoder, one-shot transport, and verification."""
        if self._source.policy is None or not self._source.policy.enabled:
            raise ServiceValidationError("writing is not enabled by policy")
        if (
            not math.isfinite(value)
            or value < self.native_min_value
            or value > self.native_max_value
        ):
            raise ServiceValidationError("value is outside the writable range")
        await self.coordinator.write_runtime.async_write(
            self._source.device_id,
            self._source.model_id,
            self._source.register_name,
            value,
        )


def _effective_range(
    source: ControlEntitySource,
) -> tuple[float | None, float | None]:
    """Intersect hard register and installation-policy semantic bounds."""
    hard = source.register.valid_range
    policy = source.policy if source.policy and source.policy.enabled else None
    minimums = [
        value
        for value in (
            hard.minimum if hard else None,
            policy.minimum if policy else None,
        )
        if value is not None
    ]
    maximums = [
        value
        for value in (
            hard.maximum if hard else None,
            policy.maximum if policy else None,
        )
        if value is not None
    ]
    return (
        max(minimums) if minimums else None,
        min(maximums) if maximums else None,
    )


def _effective_step(source: ControlEntitySource) -> float | None:
    """Use an explicit policy step or authoritative register step when present."""
    hard_step = (
        source.register.valid_range.step
        if source.register.valid_range is not None
        else None
    )
    policy = source.policy if source.policy and source.policy.enabled else None
    step = policy.step if policy and policy.step is not None else hard_step
    return float(Decimal(str(step))) if step is not None else None


def _has_finite_hard_range(source: ControlEntitySource) -> bool:
    """Require authoritative finite bounds before exposing a number entity."""
    hard = source.register.valid_range
    return (
        hard is not None
        and hard.minimum is not None
        and hard.maximum is not None
        and math.isfinite(hard.minimum)
        and math.isfinite(hard.maximum)
    )


def _entity_category(hint: EntityCategoryHint) -> EntityCategory | None:
    """Map writable entity purposes to supported Home Assistant categories."""
    return {
        EntityCategoryHint.CONFIG: EntityCategory.CONFIG,
        EntityCategoryHint.DIAGNOSTIC: EntityCategory.DIAGNOSTIC,
    }.get(hint)
