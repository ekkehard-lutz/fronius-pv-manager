"""Internal helpers for attaching entity catalog metadata to register maps."""

from collections.abc import Mapping
from dataclasses import replace

from ..models import (
    EntityCategoryHint,
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    SunSpecModelDefinition,
)


def entity(
    model_id: int,
    register_name: str,
    *,
    platform: EntityPlatform = EntityPlatform.SENSOR,
    category: EntityCategoryHint = EntityCategoryHint.PRIMARY,
    enabled: bool = True,
    role: PhysicalDeviceRole | None = None,
    block_name: str | None = None,
    translate_enum_values: bool = False,
) -> EntityDefinition:
    """Create stable Home Assistant-independent entity metadata."""
    block = f"_{block_name}" if block_name is not None else ""
    key = f"model_{model_id}{block}_{register_name.lower()}"
    return EntityDefinition(
        platform=platform,
        key=key,
        translation_key=key,
        translate_enum_values=translate_enum_values,
        category=category,
        enabled_by_default=enabled,
        device_role=role,
    )


def attach_entities(
    definition: SunSpecModelDefinition,
    fixed: dict[str, EntityDefinition],
    *,
    repeating: dict[str, dict[str, EntityDefinition]] | None = None,
) -> SunSpecModelDefinition:
    """Return a definition with catalog entries attached by register name."""
    fixed_names = {register.name for register in definition.registers}
    if unknown := set(fixed) - fixed_names:
        raise ValueError(f"unknown fixed entity registers: {sorted(unknown)}")
    registers = tuple(
        replace(register, entity=fixed.get(register.name))
        for register in definition.registers
    )
    repeating = repeating or {}
    block_names = {block.name for block in definition.repeating_blocks}
    if unknown_blocks := set(repeating) - block_names:
        raise ValueError(f"unknown repeating entity blocks: {sorted(unknown_blocks)}")
    blocks = []
    for block in definition.repeating_blocks:
        entities = repeating.get(block.name, {})
        register_names = {register.name for register in block.registers}
        if unknown := set(entities) - register_names:
            raise ValueError(
                f"unknown entity registers in block {block.name!r}: {sorted(unknown)}"
            )
        blocks.append(
            replace(
                block,
                registers=tuple(
                    replace(register, entity=entities.get(register.name))
                    for register in block.registers
                ),
            )
        )
    return replace(definition, registers=registers, repeating_blocks=tuple(blocks))


def attach_help_texts(
    definition: SunSpecModelDefinition,
    fixed: dict[str, Mapping[str, str]],
    *,
    repeating: dict[str, dict[str, Mapping[str, str]]] | None = None,
) -> SunSpecModelDefinition:
    """Return a definition with project-maintained explanatory text attached."""
    fixed_names = {register.name for register in definition.registers}
    if unknown := set(fixed) - fixed_names:
        raise ValueError(f"unknown fixed help registers: {sorted(unknown)}")
    registers = tuple(
        replace(register, help_text=fixed.get(register.name))
        for register in definition.registers
    )
    repeating = repeating or {}
    blocks = tuple(
        replace(
            block,
            registers=tuple(
                replace(
                    register,
                    help_text=repeating.get(block.name, {}).get(register.name),
                )
                for register in block.registers
            ),
        )
        for block in definition.repeating_blocks
    )
    return replace(definition, registers=registers, repeating_blocks=blocks)
