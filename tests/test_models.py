"""Tests for the Home Assistant-independent core model."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.fronius_pv_manager.models import (
    Capability,
    DeviceProfile,
    DiscoveredModel,
    DiscoveredRepeatingBlockInstance,
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RepeatingBlockDefinition,
    SunSpecModelDefinition,
    ValueRange,
)


def register(
    name: str = "power",
    offset: int = 0,
    size: int = 1,
    data_type: RegisterDataType = RegisterDataType.UINT16,
    **kwargs: object,
) -> RegisterDefinition:
    """Create a compact register definition for tests."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        **kwargs,
    )


def test_valid_register_definition() -> None:
    """A complete, consistent register definition is accepted."""
    entity = EntityDefinition(platform=EntityPlatform.SENSOR, key="power")
    definition = register(
        size=2,
        data_type=RegisterDataType.UINT32,
        unit="W",
        scale_factor="power_sf",
        valid_range=ValueRange(minimum=0, step=1),
        invalid_values=(0xFFFFFFFF,),
        entity=entity,
    )

    assert definition.offset == 0
    assert definition.poll_class.value == "normal"
    assert definition.entity is entity


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"offset": -1}, "offset"),
        ({"size": 0}, "size"),
        ({"name": "  "}, "name"),
        ({"scale_factor": ""}, "scale_factor"),
    ],
)
def test_invalid_register_definition(kwargs: dict[str, object], message: str) -> None:
    """Clearly invalid register coordinates and names are rejected."""
    with pytest.raises(ValueError, match=message):
        register(**kwargs)


def test_value_range_rejects_reversed_bounds() -> None:
    """A minimum cannot exceed its maximum."""
    with pytest.raises(ValueError, match="minimum"):
        ValueRange(minimum=2, maximum=1)


@pytest.mark.parametrize("step", [0, -0.1])
def test_value_range_rejects_non_positive_step(step: float) -> None:
    """Range increments must move values forward."""
    with pytest.raises(ValueError, match="step"):
        ValueRange(step=step)


def test_model_rejects_empty_model_ids() -> None:
    """A known layout must identify at least one SunSpec model."""
    with pytest.raises(ValueError, match="model_ids"):
        SunSpecModelDefinition((), "inverter", ())


def test_model_rejects_duplicate_model_ids() -> None:
    """A model layout cannot list the same SunSpec model ID twice."""
    with pytest.raises(ValueError, match="unique"):
        SunSpecModelDefinition((101, 101), "inverter", ())


def test_model_rejects_duplicate_register_names() -> None:
    """Register names are unambiguous within a model."""
    with pytest.raises(ValueError, match="unique"):
        SunSpecModelDefinition(
            (101,), "inverter", (register("power", 0), register("power", 1))
        )


def test_model_rejects_overlapping_register_ranges() -> None:
    """Multi-register values may not occupy another register's offset."""
    with pytest.raises(ValueError, match="overlap"):
        SunSpecModelDefinition(
            (101,), "inverter", (register("energy", 2, 2), register("power", 3))
        )


def test_model_accepts_adjacent_multi_register_definitions() -> None:
    """Adjacent multi-register values form a valid model layout."""
    definition = SunSpecModelDefinition(
        (101, 103),
        "inverter",
        (register("energy", 0, 4), register("power", 4, 2)),
        expected_length=6,
    )

    assert definition.expected_length == 6


def test_register_may_end_exactly_at_expected_length() -> None:
    """The final occupied offset may equal the declared model boundary."""
    definition = SunSpecModelDefinition(
        (101,), "inverter", (register("energy", 4, 2),), expected_length=6
    )

    assert definition.registers[0].offset + definition.registers[0].size == 6


def test_register_may_not_extend_beyond_expected_length() -> None:
    """Every register must fit completely inside the declared model length."""
    with pytest.raises(ValueError, match="expected_length"):
        SunSpecModelDefinition(
            (101,), "inverter", (register("energy", 5, 2),), expected_length=6
        )


@pytest.mark.parametrize(
    "kwargs", [{"model_id": -1}, {"base_address": -1}, {"length": 0}]
)
def test_discovered_model_validation(kwargs: dict[str, int]) -> None:
    """Discovery coordinates must identify a positive-length model."""
    values = {"model_id": 1, "base_address": 40000, "length": 65} | kwargs
    with pytest.raises(ValueError):
        DiscoveredModel(**values)


def test_device_profile_stores_models_and_capabilities() -> None:
    """A device profile retains its discovered models and capability set."""
    model = DiscoveredModel(model_id=101, base_address=40069, length=50)
    capabilities = frozenset({Capability.INVERTER, Capability.CONTROLS})

    profile = DeviceProfile(models=(model,), capabilities=capabilities)

    assert profile.models == (model,)
    assert profile.capabilities == capabilities


def test_device_profile_is_immutable() -> None:
    """A validated device profile cannot be reassigned."""
    profile = DeviceProfile(capabilities=frozenset({Capability.INVERTER}))

    with pytest.raises(FrozenInstanceError):
        profile.capabilities = frozenset()  # type: ignore[misc]


def test_entity_definition_rejects_empty_key() -> None:
    """Entity metadata needs a stable, non-empty key."""
    with pytest.raises(ValueError, match="key"):
        EntityDefinition(platform=EntityPlatform.SENSOR, key=" ")


def test_entity_definition_device_role_is_optional() -> None:
    """Existing entity metadata remains valid without a physical device role."""
    definition = EntityDefinition(platform=EntityPlatform.SENSOR, key="power")

    assert definition.device_role is None


def test_entity_definition_stores_device_role() -> None:
    """Entity metadata can identify its future owning physical device."""
    definition = EntityDefinition(
        platform=EntityPlatform.SENSOR,
        key="state_of_charge",
        device_role=PhysicalDeviceRole.STORAGE,
    )

    assert definition.device_role is PhysicalDeviceRole.STORAGE


def test_physical_device_role_values() -> None:
    """Every supported physical device role has a stable string value."""
    assert {role.value for role in PhysicalDeviceRole} == {
        "inverter",
        "storage",
        "meter",
    }


def test_valid_repeating_block_definition() -> None:
    """A block accepts adjacent block-relative register definitions."""
    block = RepeatingBlockDefinition(
        name="mppt_module",
        offset=10,
        block_size=4,
        registers=(register("dc_current", 0, 2), register("dc_voltage", 2, 2)),
    )

    assert block.offset == 10
    assert block.registers[1].offset == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": " "}, "name"),
        ({"offset": -1}, "offset"),
        ({"block_size": 0}, "size"),
    ],
)
def test_repeating_block_rejects_invalid_coordinates(
    kwargs: dict[str, object], message: str
) -> None:
    """A repeating block needs a name and valid coordinates."""
    values = {"name": "module", "offset": 0, "block_size": 2, "registers": ()}
    with pytest.raises(ValueError, match=message):
        RepeatingBlockDefinition(**(values | kwargs))


def test_repeating_block_rejects_overlapping_registers() -> None:
    """Block-relative register ranges may not overlap."""
    with pytest.raises(ValueError, match="overlap"):
        RepeatingBlockDefinition(
            "module",
            10,
            4,
            (register("current", 0, 2), register("voltage", 1, 2)),
        )


def test_repeating_block_rejects_register_outside_block() -> None:
    """Every repeated register must fit completely within one block."""
    with pytest.raises(ValueError, match="repeating block"):
        RepeatingBlockDefinition(
            "module", 10, 4, (register("energy", 3, 2),)
        )


def test_repeating_block_rejects_duplicate_register_names() -> None:
    """Register names are unique within a repeating block."""
    with pytest.raises(ValueError, match="unique"):
        RepeatingBlockDefinition(
            "module",
            10,
            4,
            (register("current", 0), register("current", 1)),
        )


def test_model_rejects_duplicate_repeating_block_names() -> None:
    """Repeating block names are unambiguous within a model definition."""
    first = RepeatingBlockDefinition("module", 10, 2, (register(),))
    second = RepeatingBlockDefinition("module", 20, 2, (register(),))

    with pytest.raises(ValueError, match="unique"):
        SunSpecModelDefinition(
            (160,), "mppt", (), repeating_blocks=(first, second)
        )


def test_repeating_block_may_end_exactly_at_expected_length() -> None:
    """The first complete block may end at the declared model boundary."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))

    model = SunSpecModelDefinition(
        (160,), "mppt", (), expected_length=12, repeating_blocks=(block,)
    )

    assert block.offset + block.block_size == model.expected_length


def test_repeating_block_may_not_extend_beyond_expected_length() -> None:
    """The first complete block must fit inside the declared model length."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))

    with pytest.raises(ValueError, match="expected_length"):
        SunSpecModelDefinition(
            (160,), "mppt", (), expected_length=11, repeating_blocks=(block,)
        )


def test_fixed_register_may_end_where_repeating_block_begins() -> None:
    """Adjacent fixed and first repeated ranges do not overlap."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))

    model = SunSpecModelDefinition(
        (160,),
        "mppt",
        (register("header", 8, 2),),
        repeating_blocks=(block,),
    )

    assert model.registers[0].offset + model.registers[0].size == block.offset


def test_fixed_register_may_not_overlap_repeating_block() -> None:
    """A fixed register cannot occupy the first repeated-block range."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))

    with pytest.raises(ValueError, match="overlaps repeating block"):
        SunSpecModelDefinition(
            (160,),
            "mppt",
            (register("header", 9, 2),),
            repeating_blocks=(block,),
        )


def test_repeating_block_definition_is_immutable() -> None:
    """A validated repeating block cannot be reassigned."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))

    with pytest.raises(FrozenInstanceError):
        block.block_size = 4  # type: ignore[misc]


def test_discovered_repeating_instance_is_immutable() -> None:
    """A concrete repeated-instance record cannot be reassigned."""
    instance = DiscoveredRepeatingBlockInstance("module", 0, 10)

    with pytest.raises(FrozenInstanceError):
        instance.instance_index = 1  # type: ignore[misc]


def test_repeating_definition_does_not_encode_instance_count() -> None:
    """One definition supports an arbitrary runtime number of instances."""
    block = RepeatingBlockDefinition("module", 10, 2, (register(),))
    model = SunSpecModelDefinition((160,), "mppt", (), repeating_blocks=(block,))
    instances = tuple(
        DiscoveredRepeatingBlockInstance(
            block_name=block.name,
            instance_index=index,
            base_offset=block.offset + index * block.block_size,
        )
        for index in range(12)
    )

    assert model.repeating_blocks == (block,)
    assert len(instances) == 12
    assert instances[-1].instance_index == 11


def test_enum_metadata_requires_enum_type() -> None:
    """Enum labels cannot be attached to an ordinary numeric type."""
    with pytest.raises(ValueError, match="enum16"):
        register(enum={0: "off"})


def test_bitfield_metadata_requires_bitfield_type() -> None:
    """Bit labels cannot be attached to an ordinary numeric type."""
    with pytest.raises(ValueError, match="bitfield"):
        register(bitfield={0: "connected"})


def test_compatible_enum_and_bitfield_metadata() -> None:
    """Enum and bitfield types accept their matching labels."""
    enum_register = register(data_type=RegisterDataType.ENUM16, enum={0: "off"})
    bitfield_register = register(
        data_type=RegisterDataType.BITFIELD32,
        size=2,
        bitfield={0: "connected"},
    )

    assert enum_register.enum == {0: "off"}
    assert bitfield_register.bitfield == {0: "connected"}


def test_capability_values() -> None:
    """Capabilities expose stable string values for later discovery logic."""
    assert {capability.value for capability in Capability} == {
        "inverter",
        "storage",
        "meter",
        "mppt",
        "controls",
        "storage_control",
        "grid_charge_control",
    }


def test_definitions_are_immutable() -> None:
    """Definitions cannot be changed after validation."""
    definition = register()
    with pytest.raises(FrozenInstanceError):
        definition.offset = 2  # type: ignore[misc]


def test_metadata_mappings_are_immutable() -> None:
    """Mutable caller mappings cannot weaken definition immutability."""
    definition = register(data_type=RegisterDataType.ENUM16, enum={0: "off"})
    assert definition.enum is not None
    with pytest.raises(TypeError):
        definition.enum[1] = "on"  # type: ignore[index]
