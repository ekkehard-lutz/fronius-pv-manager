"""Strict YAML loading for installation-specific register write policy."""

import math
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

from .models import RegisterDataType
from .register_maps import find_registers, get_model_definition
from .write_policy import WritePolicy, WritePolicyError, validate_write_policy

POLICY_VERSION = 1
DEFAULT_POLICY_PATH = Path(__file__).with_name("write_policy.default.yaml")
INSTALLATION_POLICY_DIRECTORY = "fronius_pv_manager"
INSTALLATION_POLICY_FILENAME = "write_policy.yaml"


class WritePolicyLoadError(ValueError):
    """Raised when an entire policy file cannot be safely installed."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    """Construct one mapping while rejecting ambiguous duplicate keys."""
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise WritePolicyLoadError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_write_policy_text(
    content: str,
) -> Mapping[tuple[int, str], WritePolicy]:
    """Parse and completely validate one YAML policy snapshot."""
    try:
        document = yaml.load(content, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, WritePolicyLoadError) as err:
        raise WritePolicyLoadError(f"invalid YAML: {err}") from err
    root = _mapping(document, "policy root")
    _exact_keys(root, {"version", "models"}, "policy root")
    if type(root["version"]) is not int or root["version"] != POLICY_VERSION:
        raise WritePolicyLoadError("unsupported policy version")
    models = _mapping(root["models"], "models")
    policies = {}
    for model_id, registers_value in models.items():
        if type(model_id) is not int:
            raise WritePolicyLoadError("model IDs must be integers")
        if get_model_definition(model_id) is None:
            raise WritePolicyLoadError(f"unknown model {model_id}")
        registers = _mapping(registers_value, f"model {model_id}")
        for register_name, settings_value in registers.items():
            if not isinstance(register_name, str) or not register_name:
                raise WritePolicyLoadError("register names must be non-empty strings")
            coordinate = (model_id, register_name)
            if coordinate in policies:
                raise WritePolicyLoadError(
                    f"duplicate register {model_id}:{register_name}"
                )
            policies[coordinate] = _parse_policy(
                model_id, register_name, settings_value
            )
    return MappingProxyType(policies)


def load_or_create_write_policy(config_directory: Path) -> tuple[
    Path, Mapping[tuple[int, str], WritePolicy]
]:
    """Create the installation file once if absent, then load that exact file."""
    policy_path = (
        config_directory
        / INSTALLATION_POLICY_DIRECTORY
        / INSTALLATION_POLICY_FILENAME
    )
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    if not policy_path.exists():
        default_content = DEFAULT_POLICY_PATH.read_text(encoding="utf-8")
        try:
            with policy_path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(default_content)
        except FileExistsError:
            pass
    try:
        content = policy_path.read_text(encoding="utf-8")
    except OSError as err:
        raise WritePolicyLoadError(f"cannot read policy: {err}") from err
    return policy_path, load_write_policy_text(content)


def _parse_policy(model_id: int, register_name: str, settings_value) -> WritePolicy:
    """Build one immutable policy after resolving its fixed register definition."""
    matches = find_registers(register_name, model_id=model_id)
    if not matches:
        raise WritePolicyLoadError(f"unknown register {model_id}:{register_name}")
    target = matches[0]
    if target.block_name is not None:
        raise WritePolicyLoadError("repeating-block writes are unsupported")
    definition = target.register
    settings = _mapping(settings_value, f"register {model_id}:{register_name}")
    _exact_keys(
        settings,
        {"enabled", "minimum", "maximum", "step", "values", "bits"},
        f"register {model_id}:{register_name}",
        required=set(),
    )
    minimum = _optional_number(settings, "minimum")
    maximum = _optional_number(settings, "maximum")
    step = _optional_number(settings, "step")
    enabled = _optional_boolean(settings, "enabled", default=True)
    allowed_enum_values = _enum_values(settings.get("values"), definition)
    allowed_bit_mask = _bit_mask(settings.get("bits"), definition)
    try:
        policy = WritePolicy(
            model_id=model_id,
            register_name=register_name,
            minimum=minimum,
            maximum=maximum,
            step=step,
            allowed_enum_values=allowed_enum_values,
            allowed_bit_mask=allowed_bit_mask,
            enabled=enabled,
        )
        validate_write_policy(policy, definition)
    except WritePolicyError as err:
        raise WritePolicyLoadError(
            f"invalid policy for {model_id}:{register_name}: {err}"
        ) from err
    return policy


def _mapping(value, name: str) -> dict:
    """Require a plain YAML mapping without coercion."""
    if type(value) is not dict:
        raise WritePolicyLoadError(f"{name} must be a mapping")
    return value


def _exact_keys(
    value: dict,
    allowed: set[str],
    name: str,
    *,
    required: set[str] | None = None,
) -> None:
    """Reject missing required and unexpected schema keys."""
    if any(type(key) is not str for key in value):
        raise WritePolicyLoadError(f"{name} keys must be strings")
    unexpected = set(value) - allowed
    if unexpected:
        raise WritePolicyLoadError(f"unexpected keys in {name}: {sorted(unexpected)}")
    required = allowed if required is None else required
    missing = required - set(value)
    if missing:
        raise WritePolicyLoadError(f"missing keys in {name}: {sorted(missing)}")


def _optional_number(settings: dict, key: str) -> int | float | None:
    """Return one strict finite YAML number without accepting booleans."""
    if key not in settings:
        return None
    value = settings[key]
    if type(value) not in {int, float} or not math.isfinite(value):
        raise WritePolicyLoadError(f"{key} must be a finite number")
    return value


def _optional_boolean(settings: dict, key: str, *, default: bool) -> bool:
    """Return one strict YAML boolean without accepting integer coercion."""
    if key not in settings:
        return default
    value = settings[key]
    if type(value) is not bool:
        raise WritePolicyLoadError(f"{key} must be a boolean")
    return value


def _enum_values(value, definition) -> frozenset[int] | None:
    """Resolve exact documented enum labels or numeric values."""
    if value is None:
        return None
    if definition.data_type is not RegisterDataType.ENUM16:
        raise WritePolicyLoadError("values is only valid for ENUM16 registers")
    items = _strict_list(value, "values")
    labels = {}
    for raw, label in (definition.enum or {}).items():
        if label in labels:
            raise WritePolicyLoadError(f"ambiguous documented enum label {label!r}")
        labels[label] = raw
    resolved = []
    for item in items:
        if type(item) is int and item in (definition.enum or {}):
            raw = item
        elif isinstance(item, str) and item in labels:
            raw = labels[item]
        else:
            raise WritePolicyLoadError(f"unknown enum value {item!r}")
        if raw in resolved:
            raise WritePolicyLoadError(f"duplicate enum value {item!r}")
        resolved.append(raw)
    return frozenset(resolved)


def _bit_mask(value, definition) -> int | None:
    """Resolve exact documented bit labels or masks into an allowed mask."""
    if value is None:
        return None
    if definition.data_type not in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        raise WritePolicyLoadError("bits is only valid for bitfield registers")
    items = _strict_list(value, "bits")
    labels = {}
    for mask, label in (definition.bitfield or {}).items():
        if label in labels:
            raise WritePolicyLoadError(f"ambiguous documented bit label {label!r}")
        labels[label] = mask
    resolved = []
    for item in items:
        if type(item) is int and item in (definition.bitfield or {}):
            mask = item
        elif isinstance(item, str) and item in labels:
            mask = labels[item]
        else:
            raise WritePolicyLoadError(f"unknown bit value {item!r}")
        if mask in resolved:
            raise WritePolicyLoadError(f"duplicate bit value {item!r}")
        resolved.append(mask)
    allowed = 0
    for mask in resolved:
        allowed |= mask
    return allowed


def _strict_list(value, name: str) -> list:
    """Require a YAML list without scalar coercion."""
    if type(value) is not list:
        raise WritePolicyLoadError(f"{name} must be a list")
    return value
