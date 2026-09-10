"""Policy-approved writable enum register entities."""

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FroniusPVConfigEntry
from .control_entity import (
    ControlEntitySource,
    control_entity_sources,
    current_control_raw_value,
    current_control_value,
    suggested_control_object_id,
)
from .coordinator import FroniusPVCoordinator
from .models import EntityPlatform
from .number import _entity_category
from .sensor import _device_info
from .storage_entity import StorageEntity, setup_storage_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroniusPVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create catalog select entities independently of write policy."""
    setup_storage_entities(entry, async_add_entities, _storage_modes)
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_entities():
        sources = control_entity_sources(coordinator, EntityPlatform.SELECT)
        devices_by_role = {}
        for source in sources:
            devices_by_role.setdefault(source.role, set()).add(source.device_id)
        entities = list(
            FroniusPVSelect(
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


class FroniusPVSelect(CoordinatorEntity[FroniusPVCoordinator], SelectEntity):
    """Expose one cataloged documented enum register."""

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
        self._raw_by_option = _policy_options(source)
        self.entity_description = SelectEntityDescription(
            key=source.entity.key,
            translation_key=source.translation_key,
            options=list(self._raw_by_option),
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
        self._attr_device_info = _device_info(entry_id, source, distinguish_device_name)

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
    def current_option(self) -> str | None:
        """Map the latest confirmed enum semantic to its stable option key."""
        if self._source.register.bitfield is not None:
            raw = current_control_raw_value(self.coordinator, self._source)
            option = str(raw) if type(raw) is int else None
            return option if option in self.options else None
        value = current_control_value(self.coordinator, self._source)
        documented = self._source.register.enum or {}
        raw = next(
            (raw for raw, label in documented.items() if label == value),
            value if type(value) is int else None,
        )
        option = str(raw) if raw is not None else None
        return option if option in self.options else None

    @property
    def suggested_object_id(self) -> str:
        """Return a stable language-independent low-level object ID."""
        return suggested_control_object_id(self._source)

    async def async_select_option(self, option: str) -> None:
        """Resolve a documented option and use the verified write runtime."""
        if self._source.policy is None or not self._source.policy.enabled:
            raise ServiceValidationError("writing is not enabled by policy")
        raw = self._raw_by_option.get(option)
        if raw is None:
            raise ServiceValidationError("option is not approved for this entity")
        await self.coordinator.write_runtime.async_write(
            self._source.device_id,
            self._source.model_id,
            self._source.register_name,
            raw,
        )


def _policy_options(source: ControlEntitySource) -> dict[str, int]:
    """Return stable raw-value keys narrowed by the runtime policy subset."""
    if source.register.bitfield is not None:
        documented_mask = 0
        for mask in source.register.bitfield:
            documented_mask |= mask
        policy = source.policy if source.policy and source.policy.enabled else None
        allowed_mask = (
            policy.allowed_bit_mask
            if policy and policy.allowed_bit_mask is not None
            else documented_mask
        )
        return {
            str(raw): raw
            for raw in range(documented_mask + 1)
            if raw & ~allowed_mask == 0
        }
    documented = source.register.enum or {}
    policy = source.policy if source.policy and source.policy.enabled else None
    allowed = policy.allowed_enum_values if policy else None
    return {str(raw): raw for raw in documented if allowed is None or raw in allowed}


class StorageMode(StorageEntity, SelectEntity):
    """Select a verified storage mode without discarding watt settings."""

    _attr_options = ["automatic", "manual", "remote"]

    @property
    def current_option(self):
        try:
            self.control.snapshot(self._source.device_id)
            return self.control.mode(self._source.device_id)
        except ServiceValidationError:
            return None

    async def async_select_option(self, option):
        if option not in self.options:
            raise ServiceValidationError("unknown storage operating mode")
        await self.change(mode=option)


def _storage_modes(coordinator, entry_id, source):
    return [StorageMode(coordinator, entry_id, source, "operating_mode")]
