"""Tests for the Home Assistant-independent entity catalog."""

import pytest

from custom_components.fronius_pv_manager.models import (
    EntityCategoryHint,
    EntityPlatform,
    PhysicalDeviceRole,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
)
from custom_components.fronius_pv_manager.register_maps import (
    MODEL_1,
    MODEL_103,
    MODEL_120,
    MODEL_121,
    MODEL_122,
    MODEL_123,
    MODEL_124,
    MODEL_160,
    MODEL_203,
)

MODELS = (
    MODEL_1,
    MODEL_103,
    MODEL_120,
    MODEL_121,
    MODEL_122,
    MODEL_123,
    MODEL_124,
    MODEL_160,
    MODEL_203,
)


def register(model: SunSpecModelDefinition, name: str) -> RegisterDefinition:
    """Find one fixed register by its unique name."""
    return next(item for item in model.registers if item.name == name)


def repeated_register(name: str) -> RegisterDefinition:
    """Find one Model 160 module register by its unique name."""
    return next(
        item for item in MODEL_160.repeating_blocks[0].registers if item.name == name
    )


def all_registers(model: SunSpecModelDefinition):
    """Yield fixed and repeating definitions from one model."""
    yield from model.registers
    for block in model.repeating_blocks:
        yield from block.registers


def assert_entity(
    definition: RegisterDefinition,
    platform: EntityPlatform,
    category: EntityCategoryHint,
    enabled: bool,
    role: PhysicalDeviceRole | None,
) -> None:
    """Assert all independent catalog dimensions for one register."""
    assert definition.entity is not None
    assert definition.entity.platform is platform
    assert definition.entity.category is category
    assert definition.entity.enabled_by_default is enabled
    assert definition.entity.device_role is role


def test_common_identity_stays_raw_for_future_device_info() -> None:
    """Model 1 identity metadata is not duplicated as diagnostic entities."""
    assert all(item.entity is None for item in MODEL_1.registers)


def test_scale_factors_and_padding_are_never_entities() -> None:
    """Implementation-only fields remain available solely as raw metadata."""
    for model in MODELS:
        for item in all_registers(model):
            if item.data_type is RegisterDataType.SUNSSF or "pad" in item.name.lower():
                assert item.entity is None


def test_writable_entity_controls_are_config_and_disabled() -> None:
    """Low-level write-capable catalog entries are opt-in configuration."""
    for model in MODELS:
        for item in all_registers(model):
            if item.access is RegisterAccess.READ_WRITE and item.entity is not None:
                assert item.entity.category is EntityCategoryHint.CONFIG
                assert not item.entity.enabled_by_default


@pytest.mark.parametrize(
    "model", [MODEL_103, MODEL_120, MODEL_121, MODEL_122, MODEL_123]
)
def test_inverter_catalog_entries_have_inverter_role(
    model: SunSpecModelDefinition,
) -> None:
    """Whole-model inverter entries have explicit inverter ownership."""
    assert all(
        item.entity.device_role is PhysicalDeviceRole.INVERTER
        for item in all_registers(model)
        if item.entity is not None
    )


def test_storage_and_meter_catalog_roles() -> None:
    """Storage and meter entries retain their distinct physical ownership."""
    assert all(
        item.entity.device_role is PhysicalDeviceRole.STORAGE
        for item in MODEL_124.registers
        if item.entity is not None
    )
    assert all(
        item.entity.device_role is PhysicalDeviceRole.METER
        for item in MODEL_203.registers
        if item.entity is not None
    )


def test_model_103_representative_catalog() -> None:
    """Core inverter values are enabled while technical events are opt-in."""
    for name in ("W", "Hz", "WH"):
        assert_entity(
            register(MODEL_103, name),
            EntityPlatform.SENSOR,
            EntityCategoryHint.PRIMARY,
            True,
            PhysicalDeviceRole.INVERTER,
        )
    assert (
        register(MODEL_103, "TmpCab").entity.category
        is EntityCategoryHint.DIAGNOSTIC
    )
    assert_entity(
        register(MODEL_103, "Evt1"),
        EntityPlatform.SENSOR,
        EntityCategoryHint.DIAGNOSTIC,
        False,
        PhysicalDeviceRole.INVERTER,
    )
    assert register(MODEL_103, "DCW").entity.enabled_by_default
    for name in (
        "AphA",
        "AphB",
        "AphC",
        "PhVphA",
        "PhVphB",
        "PhVphC",
        "PPVphAB",
        "PPVphBC",
        "PPVphCA",
    ):
        assert register(MODEL_103, name).entity.enabled_by_default
    assert not register(MODEL_103, "TmpCab").entity.enabled_by_default


def test_model_124_representative_catalog() -> None:
    """Storage operations are primary and low-level controls are opt-in."""
    for name in ("ChaState", "ChaSt"):
        assert_entity(
            register(MODEL_124, name),
            EntityPlatform.SENSOR,
            EntityCategoryHint.PRIMARY,
            True,
            PhysicalDeviceRole.STORAGE,
        )
    assert_entity(
        register(MODEL_124, "ChaGriSet"),
        EntityPlatform.SELECT,
        EntityCategoryHint.CONFIG,
        False,
        PhysicalDeviceRole.STORAGE,
    )
    assert_entity(
        register(MODEL_124, "OutWRte"),
        EntityPlatform.NUMBER,
        EntityCategoryHint.CONFIG,
        False,
        PhysicalDeviceRole.STORAGE,
    )
    assert register(MODEL_124, "StorCtl_Mod").entity is None
    assert_entity(
        register(MODEL_124, "WChaMax"),
        EntityPlatform.SENSOR,
        EntityCategoryHint.DIAGNOSTIC,
        False,
        PhysicalDeviceRole.STORAGE,
    )


def test_model_160_repeating_catalog_uses_runtime_role() -> None:
    """Shared MPPT/storage module definitions do not preselect an owner."""
    for name in ("DCW", "DCWH"):
        assert_entity(
            repeated_register(name),
            EntityPlatform.SENSOR,
            EntityCategoryHint.PRIMARY,
            True,
            None,
        )
    assert_entity(
        repeated_register("DCEvt"),
        EntityPlatform.SENSOR,
        EntityCategoryHint.DIAGNOSTIC,
        False,
        None,
    )
    for name in ("DCA", "DCV"):
        assert_entity(
            repeated_register(name),
            EntityPlatform.SENSOR,
            EntityCategoryHint.PRIMARY,
            True,
            None,
        )


def test_model_203_representative_catalog() -> None:
    """Core meter power, voltage, and energy values are primary entities."""
    for name in ("W", "PhVphA", "PhVphB", "PhVphC", "TotWhExp", "TotWhImp"):
        assert_entity(
            register(MODEL_203, name),
            EntityPlatform.SENSOR,
            EntityCategoryHint.PRIMARY,
            True,
            PhysicalDeviceRole.METER,
        )
    assert_entity(
        register(MODEL_203, "Evt"),
        EntityPlatform.SENSOR,
        EntityCategoryHint.DIAGNOSTIC,
        False,
        PhysicalDeviceRole.METER,
    )
    for name in ("AphA", "AphB", "AphC"):
        assert register(MODEL_203, name).entity.enabled_by_default
    for name in ("VA", "VAR", "PF"):
        assert not register(MODEL_203, name).entity.enabled_by_default


def test_model_123_control_platforms() -> None:
    """Immediate controls describe intended numeric and boolean platforms."""
    assert_entity(
        register(MODEL_123, "WMaxLimPct"),
        EntityPlatform.NUMBER,
        EntityCategoryHint.CONFIG,
        False,
        PhysicalDeviceRole.INVERTER,
    )
    assert_entity(
        register(MODEL_123, "WMaxLim_Ena"),
        EntityPlatform.SWITCH,
        EntityCategoryHint.CONFIG,
        False,
        PhysicalDeviceRole.INVERTER,
    )


def test_duplicate_inverter_energy_is_disabled_by_default() -> None:
    """Model 103 remains the preferred default lifetime inverter energy source."""
    assert register(MODEL_103, "WH").entity.enabled_by_default
    assert not register(MODEL_122, "ActWh").entity.enabled_by_default
