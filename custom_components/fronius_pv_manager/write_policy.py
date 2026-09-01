"""Home Assistant-independent safety policy for semantic register writes."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .models import RegisterAccess, RegisterDataType, RegisterDefinition
from .register_maps import find_registers


class WriteSafetyClass(StrEnum):
    """Integration-level safety classification for an approved write."""

    USER_CONFIGURATION = "user_configuration"


class WritePolicyError(ValueError):
    """Raised when a policy or requested semantic value is unsafe."""


@dataclass(frozen=True, slots=True)
class WritePolicy:
    """Immutable allow-list entry that may narrow protocol constraints."""

    model_id: int
    register_name: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    allowed_enum_values: frozenset[int] | None = None
    allowed_bit_mask: int | None = None
    safety_class: WriteSafetyClass = WriteSafetyClass.USER_CONFIGURATION
    enabled: bool = True

    def __post_init__(self) -> None:
        """Reject internally inconsistent policy metadata."""
        if self.model_id < 0:
            raise WritePolicyError("model ID must be non-negative")
        if not self.register_name.strip():
            raise WritePolicyError("register name must not be empty")
        if type(self.enabled) is not bool:
            raise WritePolicyError("enabled must be a boolean")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise WritePolicyError("minimum must not be greater than maximum")
        if self.step is not None and self.step <= 0:
            raise WritePolicyError("step must be positive")
        if self.allowed_enum_values is not None:
            object.__setattr__(
                self, "allowed_enum_values", frozenset(self.allowed_enum_values)
            )
        if self.allowed_bit_mask is not None and self.allowed_bit_mask < 0:
            raise WritePolicyError("allowed bit mask must not be negative")


def validate_write_policy(
    policy: WritePolicy, definition: RegisterDefinition
) -> None:
    """Ensure a policy only narrows the supplied protocol definition."""
    if definition.name != policy.register_name:
        raise WritePolicyError("policy register name does not match definition")
    if definition.access is not RegisterAccess.READ_WRITE:
        raise WritePolicyError("policy cannot approve a read-only register")
    value_range = definition.valid_range
    if value_range is not None:
        if (
            value_range.minimum is not None
            and policy.minimum is not None
            and policy.minimum < value_range.minimum
        ):
            raise WritePolicyError("policy broadens the register minimum")
        if (
            value_range.maximum is not None
            and policy.maximum is not None
            and policy.maximum > value_range.maximum
        ):
            raise WritePolicyError("policy broadens the register maximum")
        if value_range.step is not None:
            if policy.step is not None:
                ratio = Decimal(str(policy.step)) / Decimal(str(value_range.step))
                if ratio != ratio.to_integral_value():
                    raise WritePolicyError("policy broadens the register step")

    if policy.allowed_enum_values is not None:
        if definition.data_type is not RegisterDataType.ENUM16:
            raise WritePolicyError("enum subset requires an enum register")
        if not policy.allowed_enum_values:
            raise WritePolicyError("enum subset must not be empty")
        documented = frozenset(definition.enum or {})
        if not policy.allowed_enum_values <= documented:
            raise WritePolicyError("policy allows an undocumented enum value")

    if policy.allowed_bit_mask is not None:
        if definition.data_type not in {
            RegisterDataType.BITFIELD16,
            RegisterDataType.BITFIELD32,
        }:
            raise WritePolicyError("bit mask requires a bitfield register")
        documented_mask = 0
        for mask in definition.bitfield or {}:
            documented_mask |= mask
        if policy.allowed_bit_mask & ~documented_mask:
            raise WritePolicyError("policy allows undocumented bits")


def validate_policy_value(
    policy: WritePolicy, definition: RegisterDefinition, value: object
) -> None:
    """Validate a semantic value against policy-only restrictions."""
    validate_write_policy(policy, definition)
    if not policy.enabled:
        raise WritePolicyError("writes are disabled by policy")
    if definition.data_type is RegisterDataType.ENUM16:
        if (
            policy.allowed_enum_values is not None
            and value not in policy.allowed_enum_values
        ):
            raise WritePolicyError("enum value is not approved by policy")
        return
    if definition.data_type in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        if type(value) is not int:
            raise WritePolicyError("bitfield policy value must be an integer")
        if policy.allowed_bit_mask is not None and value & ~policy.allowed_bit_mask:
            raise WritePolicyError("bitfield value contains policy-disallowed bits")
        return
    try:
        requested = Decimal(str(value))
    except (InvalidOperation, ValueError) as err:
        raise WritePolicyError("policy value must be numeric") from err
    minimum = Decimal(str(policy.minimum)) if policy.minimum is not None else None
    maximum = Decimal(str(policy.maximum)) if policy.maximum is not None else None
    if minimum is not None and requested < minimum:
        raise WritePolicyError("value is below the policy minimum")
    if maximum is not None and requested > maximum:
        raise WritePolicyError("value is above the policy maximum")
    if policy.step is not None:
        step = Decimal(str(policy.step))
        origin = minimum or Decimal(0)
        if (requested - origin) % step:
            raise WritePolicyError("value does not match the policy step")


def resolve_policy_definition(policy: WritePolicy) -> RegisterDefinition:
    """Resolve and validate the fixed register targeted by a policy."""
    matches = find_registers(policy.register_name, model_id=policy.model_id)
    if not matches or matches[0].block_name is not None:
        raise WritePolicyError("policy target is not a known fixed register")
    definition = matches[0].register
    validate_write_policy(policy, definition)
    return definition
