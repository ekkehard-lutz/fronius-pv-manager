"""Tests for the explicit Home Assistant-independent write allow-list."""

from dataclasses import FrozenInstanceError, replace

import pytest

from custom_components.fronius_pv_manager.models import ValueRange
from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.write_policy import (
    WritePolicy,
    WritePolicyError,
    resolve_policy_definition,
    validate_policy_value,
    validate_write_policy,
)

MINIMUM_STORAGE_RESERVE_POLICY = WritePolicy(124, "MinRsvPct", 0, 100)


def register(name):
    """Return one fixed Model 124 register definition."""
    return next(item for item in MODEL_124.registers if item.name == name)


def test_minimum_storage_reserve_policy_is_semantic_and_immutable() -> None:
    """The initial policy stores semantic bounds but no live addressing state."""
    policy = MINIMUM_STORAGE_RESERVE_POLICY
    assert policy.minimum == 0
    assert policy.maximum == 100
    assert policy.step is None
    assert policy.enabled
    assert resolve_policy_definition(policy) is register("MinRsvPct")
    assert not hasattr(policy, "transport_address")
    assert not hasattr(policy, "scale_factor")
    with pytest.raises(FrozenInstanceError):
        policy.maximum = 101  # type: ignore[misc]


@pytest.mark.parametrize("value", [0, 7, 10, 100])
def test_minimum_storage_reserve_accepts_policy_range(value: int) -> None:
    """Documented percentage endpoints and ordinary values are approved."""
    validate_policy_value(
        MINIMUM_STORAGE_RESERVE_POLICY, register("MinRsvPct"), value
    )


@pytest.mark.parametrize("value", [-1, 101])
def test_minimum_storage_reserve_rejects_outside_policy_range(value: int) -> None:
    """The policy rejects semantic values outside zero through one hundred."""
    with pytest.raises(WritePolicyError):
        validate_policy_value(
            MINIMUM_STORAGE_RESERVE_POLICY, register("MinRsvPct"), value
        )


def test_policy_cannot_broaden_protocol_range_or_approve_read_only() -> None:
    """Policy validation preserves protocol bounds, increments, and access."""
    constrained = replace(register("MinRsvPct"), valid_range=ValueRange(0, 100, 1))
    for policy in (
        WritePolicy(124, "MinRsvPct", minimum=-1, maximum=100, step=1),
        WritePolicy(124, "MinRsvPct", minimum=0, maximum=101, step=1),
        WritePolicy(124, "MinRsvPct", minimum=0, maximum=100, step=0.5),
    ):
        with pytest.raises(WritePolicyError):
            validate_write_policy(policy, constrained)
    with pytest.raises(WritePolicyError, match="read-only"):
        validate_write_policy(
            WritePolicy(124, "ChaState"), register("ChaState")
        )


def test_enum_policy_can_only_narrow_documented_values() -> None:
    """A future enum policy cannot approve undocumented protocol choices."""
    definition = register("ChaGriSet")
    with pytest.raises(WritePolicyError, match="undocumented enum"):
        validate_write_policy(
            WritePolicy(124, "ChaGriSet", allowed_enum_values=frozenset({2})),
            definition,
        )
    charge_only = WritePolicy(
        124, "ChaGriSet", allowed_enum_values=frozenset({0})
    )
    validate_policy_value(charge_only, definition, 0)
    with pytest.raises(WritePolicyError, match="not approved"):
        validate_policy_value(charge_only, definition, 1)


def test_policy_enabled_requires_an_actual_boolean() -> None:
    """The immutable policy model rejects Python's boolean-like values."""
    assert WritePolicy(124, "MinRsvPct", enabled=False).enabled is False
    for value in (0, 1, "true", None, [], {}):
        with pytest.raises(WritePolicyError, match="boolean"):
            WritePolicy(124, "MinRsvPct", enabled=value)  # type: ignore[arg-type]


def test_disabled_policy_rejects_values_after_definition_validation() -> None:
    """Disabled policy metadata remains valid but authorizes no semantic value."""
    policy = WritePolicy(124, "MinRsvPct", 5, 20, enabled=False)
    validate_write_policy(policy, register("MinRsvPct"))
    with pytest.raises(WritePolicyError, match="disabled"):
        validate_policy_value(policy, register("MinRsvPct"), 10)


def test_empty_enum_subset_is_not_a_valid_policy() -> None:
    """An enabled SELECT policy must retain at least one documented option."""
    with pytest.raises(WritePolicyError, match="must not be empty"):
        validate_write_policy(
            WritePolicy(124, "ChaGriSet", allowed_enum_values=frozenset()),
            register("ChaGriSet"),
        )


def test_bitfield_policy_restricts_documented_mask_subset() -> None:
    """Future bitfield policies may narrow but never broaden known bits."""
    definition = register("StorCtl_Mod")
    with pytest.raises(WritePolicyError, match="undocumented bits"):
        validate_write_policy(
            WritePolicy(124, "StorCtl_Mod", allowed_bit_mask=0x0004), definition
        )
    charge_only = WritePolicy(124, "StorCtl_Mod", allowed_bit_mask=0x0001)
    for value in (0, 1):
        validate_policy_value(charge_only, definition, value)
    for value in (2, 3):
        with pytest.raises(WritePolicyError, match="policy-disallowed"):
            validate_policy_value(charge_only, definition, value)
