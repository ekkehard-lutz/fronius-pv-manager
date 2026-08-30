"""Decode complete SunSpec model payloads from immutable definitions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .codec import decode_register_value
from .models import (
    RegisterDefinition,
    RegisterValue,
    RepeatingBlockDefinition,
    SunSpecModelDefinition,
)


@dataclass(frozen=True, slots=True)
class DecodedRepeatingBlockInstance:
    """Decoded values for one zero-based repeating-block instance."""

    instance_index: int
    base_offset: int
    values: Mapping[str, RegisterValue]

    def __post_init__(self) -> None:
        """Defensively freeze the decoded value mapping."""
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class DecodedModel:
    """Decoded fixed values and repeating instances for one model payload."""

    fixed: Mapping[str, RegisterValue]
    repeating: Mapping[str, tuple[DecodedRepeatingBlockInstance, ...]]

    def __post_init__(self) -> None:
        """Defensively freeze both result mappings and instance sequences."""
        object.__setattr__(self, "fixed", MappingProxyType(dict(self.fixed)))
        object.__setattr__(
            self,
            "repeating",
            MappingProxyType(
                {name: tuple(instances) for name, instances in self.repeating.items()}
            ),
        )


def decode_model(
    definition: SunSpecModelDefinition,
    payload: Sequence[int],
) -> DecodedModel:
    """Decode a model-data payload that excludes the SunSpec ID/length header."""
    words = tuple(payload)
    if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
        raise ValueError("payload words must be integers from 0 through 65535")

    fixed = _decode_fixed(definition.registers, words)
    repeating = _decode_repeating(definition.repeating_blocks, words, fixed)
    return DecodedModel(fixed=fixed, repeating=repeating)


def _decode_fixed(
    definitions: tuple[RegisterDefinition, ...],
    payload: tuple[int, ...],
) -> dict[str, RegisterValue]:
    """Decode fixed registers and then apply resolved fixed scale factors."""
    _validate_scale_factor_references(definitions, definitions)
    unscaled = {
        register.name: decode_register_value(
            register, _register_words(register, payload)
        )
        for register in definitions
    }
    return {
        register.name: _decode_with_scale_factor(
            register,
            _register_words(register, payload),
            unscaled,
        )
        for register in definitions
    }


def _decode_repeating(
    blocks: tuple[RepeatingBlockDefinition, ...],
    payload: tuple[int, ...],
    fixed: Mapping[str, RegisterValue],
) -> dict[str, tuple[DecodedRepeatingBlockInstance, ...]]:
    """Decode every structurally complete instance in each repeating region."""
    ordered_blocks = sorted(blocks, key=lambda block: block.offset)
    repeating: dict[str, tuple[DecodedRepeatingBlockInstance, ...]] = {}

    for index, block in enumerate(ordered_blocks):
        _validate_scale_factor_references(block.registers, tuple(), fixed)
        if (
            index + 1 < len(ordered_blocks)
            and ordered_blocks[index + 1].offset < block.offset + block.block_size
        ):
            raise ValueError(
                f"repeating block {ordered_blocks[index + 1].name!r} overlaps "
                f"the first instance of {block.name!r}"
            )
        region_end = (
            ordered_blocks[index + 1].offset
            if index + 1 < len(ordered_blocks)
            else len(payload)
        )
        if block.offset > len(payload):
            raise ValueError(
                f"payload is too short for repeating block {block.name!r}"
            )
        available = region_end - block.offset
        if available < 0:
            raise ValueError("repeating block offsets are not structurally ordered")
        instance_count, remainder = divmod(available, block.block_size)
        if remainder:
            raise ValueError(
                f"repeating block {block.name!r} has {remainder} trailing registers"
            )

        instances = tuple(
            _decode_repeating_instance(block, instance_index, payload, fixed)
            for instance_index in range(instance_count)
        )
        repeating[block.name] = instances

    return repeating


def _decode_repeating_instance(
    block: RepeatingBlockDefinition,
    instance_index: int,
    payload: tuple[int, ...],
    fixed: Mapping[str, RegisterValue],
) -> DecodedRepeatingBlockInstance:
    """Decode one repeating instance at its computed model-relative base."""
    base_offset = block.offset + instance_index * block.block_size
    values: dict[str, RegisterValue] = {}
    for register in block.registers:
        absolute_register = RegisterDefinition(
            name=register.name,
            offset=base_offset + register.offset,
            size=register.size,
            data_type=register.data_type,
            access=register.access,
            unit=register.unit,
            scale_factor=register.scale_factor,
            description=register.description,
            valid_range=register.valid_range,
            enum=register.enum,
            bitfield=register.bitfield,
            invalid_values=register.invalid_values,
            entity=register.entity,
            poll_class=register.poll_class,
        )
        raw_words = _register_words(absolute_register, payload)
        values[register.name] = _decode_with_scale_factor(
            register, raw_words, fixed
        )
    return DecodedRepeatingBlockInstance(
        instance_index=instance_index,
        base_offset=base_offset,
        values=values,
    )


def _register_words(
    register: RegisterDefinition,
    payload: tuple[int, ...],
) -> tuple[int, ...]:
    """Return a complete register slice or reject a short payload."""
    end = register.offset + register.size
    if end > len(payload):
        raise ValueError(
            f"payload is too short for register {register.name!r} at offset "
            f"{register.offset}"
        )
    return payload[register.offset:end]


def _validate_scale_factor_references(
    dependents: tuple[RegisterDefinition, ...],
    fixed_definitions: tuple[RegisterDefinition, ...],
    fixed_values: Mapping[str, RegisterValue] | None = None,
) -> None:
    """Reject named scale-factor sources that are absent or non-integral."""
    fixed_names = {register.name for register in fixed_definitions}
    if fixed_values is not None:
        fixed_names.update(fixed_values)
    for register in dependents:
        if register.scale_factor is None:
            continue
        if register.scale_factor not in fixed_names:
            raise ValueError(
                f"scale factor {register.scale_factor!r} referenced by "
                f"{register.name!r} is not a fixed register"
            )
        if fixed_values is not None:
            value = fixed_values[register.scale_factor].value
            if value is not None and type(value) is not int:
                raise ValueError(
                    f"scale factor {register.scale_factor!r} must decode to an integer"
                )


def _decode_with_scale_factor(
    register: RegisterDefinition,
    words: tuple[int, ...],
    fixed: Mapping[str, RegisterValue],
) -> RegisterValue:
    """Decode with a resolved fixed scale factor or propagate its invalidity."""
    unscaled = decode_register_value(register, words)
    if register.scale_factor is None:
        return unscaled
    scale_factor = fixed[register.scale_factor].value
    if scale_factor is None:
        return RegisterValue(raw=unscaled.raw, value=None)
    if type(scale_factor) is not int:
        raise ValueError(
            f"scale factor {register.scale_factor!r} must decode to an integer"
        )
    return decode_register_value(register, words, scale_factor=scale_factor)


__all__ = ["DecodedModel", "DecodedRepeatingBlockInstance", "decode_model"]
