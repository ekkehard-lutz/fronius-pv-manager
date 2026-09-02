"""Tests for capability inference and runtime semantic classification."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.fronius_pv_manager.model_decoder import (
    DecodedModel,
    DecodedRepeatingBlockInstance,
    decode_model,
)
from custom_components.fronius_pv_manager.models import (
    Capability,
    DeviceProfile,
    DiscoveredModel,
    PhysicalDeviceRole,
    RegisterValue,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_160
from custom_components.fronius_pv_manager.semantics import (
    Model160ModuleKind,
    augment_profile_with_model_160,
    classify_model_160_module,
    classify_model_160_modules,
    infer_device_profile,
    physical_role_for_model,
)


def _model(model_id: int) -> DiscoveredModel:
    return DiscoveredModel(model_id=model_id, base_address=40004, length=1)


def _words(value: str) -> list[int]:
    data = value.encode("ascii").ljust(16, b"\0")
    return [int.from_bytes(data[index : index + 2], "big") for index in range(0, 16, 2)]


def _real_model_160() -> DecodedModel:
    payload = [0xFFFD, 0xFFFE, 0xFFFF, 0xFFFE, 0xFFFF, 0xFFFF, 4, 0xFFFF]
    for module_id, name in (
        (1, "MPPT 1"),
        (2, "MPPT 2"),
        (3, "StCha 3"),
        (4, "StDisCha 4"),
    ):
        payload.extend([module_id, *_words(name), *([0] * 11)])
    return decode_model(MODEL_160, payload)


def _instance(name: str | None, *, include_name: bool = True):
    values = {"ID": RegisterValue(raw=7, value=7)}
    if include_name:
        values["IDStr"] = RegisterValue(raw=name, value=name)
    return DecodedRepeatingBlockInstance(9, 188, values)


def test_static_capabilities_are_inferred_from_models_and_writable_metadata() -> None:
    models = tuple(_model(model_id) for model_id in (1, 103, 123, 124, 160))
    profile = infer_device_profile(models)

    assert profile.models == models
    assert profile.capabilities == frozenset(
        {
            Capability.INVERTER,
            Capability.STORAGE,
            Capability.CONTROLS,
            Capability.STORAGE_CONTROL,
            Capability.GRID_CHARGE_CONTROL,
        }
    )
    assert Capability.MPPT not in profile.capabilities


def test_meter_and_unknown_models_have_only_supported_capabilities() -> None:
    assert infer_device_profile((_model(203),)).capabilities == frozenset(
        {Capability.METER}
    )
    assert not infer_device_profile((_model(999),)).capabilities


def test_real_model_160_modules_are_classified_by_identity_not_position() -> None:
    modules = classify_model_160_modules(_real_model_160())

    assert [(item.instance_index, item.module_id) for item in modules] == [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    ]
    assert [item.semantic_kind for item in modules] == [
        Model160ModuleKind.MPPT,
        Model160ModuleKind.MPPT,
        Model160ModuleKind.STORAGE_CHARGE,
        Model160ModuleKind.STORAGE_DISCHARGE,
    ]
    assert [item.physical_role for item in modules] == [
        PhysicalDeviceRole.INVERTER,
        PhysicalDeviceRole.INVERTER,
        PhysicalDeviceRole.STORAGE,
        PhysicalDeviceRole.STORAGE,
    ]


@pytest.mark.parametrize(
    ("instance", "expected_name"),
    [
        (_instance("unknown module"), "unknown module"),
        (_instance(""), None),
        (_instance("   "), None),
        (_instance(None), None),
        (_instance(None, include_name=False), None),
    ],
)
def test_unknown_or_absent_module_identity_is_neutral(instance, expected_name) -> None:
    classified = classify_model_160_module(instance)
    assert classified.module_name == expected_name
    assert classified.semantic_kind is Model160ModuleKind.UNKNOWN
    assert classified.physical_role is None


def test_classification_uses_runtime_instances_without_fixed_count_or_index() -> None:
    classified = classify_model_160_module(_instance("  MPPT auxiliary  "))
    assert classified.instance_index == 9
    assert classified.module_name == "MPPT auxiliary"
    assert classified.semantic_kind is Model160ModuleKind.MPPT


def test_runtime_augmentation_returns_new_profile_and_preserves_original() -> None:
    original = infer_device_profile((_model(103), _model(160)))
    augmented = augment_profile_with_model_160(original, _real_model_160())

    assert augmented is not original
    assert Capability.MPPT not in original.capabilities
    assert Capability.MPPT in augmented.capabilities


def test_non_mppt_modules_do_not_add_mppt_capability() -> None:
    decoded = DecodedModel({}, {"module": (_instance("StCha 7"),)})
    original = DeviceProfile((_model(160),), frozenset())
    assert Capability.MPPT not in augment_profile_with_model_160(
        original, decoded
    ).capabilities


def test_classification_result_is_immutable() -> None:
    classified = classify_model_160_module(_instance("MPPT 1"))
    with pytest.raises(FrozenInstanceError):
        classified.module_id = 12  # type: ignore[misc]


@pytest.mark.parametrize(
    ("model_id", "role"),
    [
        (103, PhysicalDeviceRole.INVERTER),
        (120, PhysicalDeviceRole.INVERTER),
        (121, PhysicalDeviceRole.INVERTER),
        (122, PhysicalDeviceRole.INVERTER),
        (123, PhysicalDeviceRole.INVERTER),
        (124, PhysicalDeviceRole.STORAGE),
        (203, PhysicalDeviceRole.METER),
        (1, None),
        (160, None),
        (999, None),
    ],
)
def test_whole_model_physical_role(model_id, role) -> None:
    assert physical_role_for_model(model_id) is role
