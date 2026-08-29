"""Tests for the Home Assistant-independent core model."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.fronius_pv_manager.models import (
    Capability,
    DeviceProfile,
    DiscoveredModel,
    EntityDefinition,
    EntityPlatform,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
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
