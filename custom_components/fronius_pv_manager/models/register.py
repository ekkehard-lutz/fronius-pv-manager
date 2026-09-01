"""SunSpec register and model definitions.

Addressing is deliberately split into three distinct concepts:

* Fronius documentation register numbers (for example, 40356) are source
  material used while reviewing register maps.
* SunSpec model-relative offsets locate registers within a discovered model and
  are the only addresses stored by :class:`RegisterDefinition`.
* pymodbus zero-based addresses belong exclusively to the future transport
  layer, where model bases and transport conventions can be applied together.

Keeping these address spaces separate prevents vendor documentation details or
transport-library conventions from leaking into reusable model definitions.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .role import PhysicalDeviceRole


class RegisterDataType(StrEnum):
    """Data types used by SunSpec register definitions."""

    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    UINT64 = "uint64"
    SUNSSF = "sunssf"
    ENUM16 = "enum16"
    BITFIELD16 = "bitfield16"
    BITFIELD32 = "bitfield32"
    ACC32 = "acc32"
    ACC64 = "acc64"
    STRING = "string"


class RegisterAccess(StrEnum):
    """Supported register access modes."""

    READ_ONLY = "read"
    READ_WRITE = "read_write"


class PollClass(StrEnum):
    """Relative polling groups, without prescribing concrete intervals."""

    STATIC = "static"
    FAST = "fast"
    NORMAL = "normal"
    SLOW = "slow"


class EntityPlatform(StrEnum):
    """Home Assistant platform metadata without a Home Assistant dependency."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    NUMBER = "number"
    SELECT = "select"
    SWITCH = "switch"


class EntityCategoryHint(StrEnum):
    """Home Assistant-independent presentation purpose for a future entity."""

    PRIMARY = "primary"
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


@dataclass(frozen=True, slots=True)
class ValueRange:
    """Optional bounds and increment metadata for a decoded value."""

    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    def __post_init__(self) -> None:
        """Validate internally inconsistent range metadata."""
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum must not be greater than maximum")
        if self.step is not None and self.step <= 0:
            raise ValueError("step must be positive")


@dataclass(frozen=True, slots=True)
class EntityDefinition:
    """Optional metadata for a future Home Assistant entity."""

    platform: EntityPlatform
    key: str
    device_class: str | None = None
    state_class: str | None = None
    default_enabled: bool = True
    entity_category: str | None = None
    device_role: PhysicalDeviceRole | None = None
    category: EntityCategoryHint = EntityCategoryHint.PRIMARY
    enabled_by_default: bool = True
    translation_key: str | None = None
    translate_enum_values: bool = False
    presentation_unit: str | None = None
    suggested_object_id: str | None = None

    def __post_init__(self) -> None:
        """Reject metadata that cannot identify an entity."""
        if not self.key.strip():
            raise ValueError("entity key must not be empty")
        if self.translation_key is not None and not self.translation_key.strip():
            raise ValueError("entity translation key must not be empty")
        if self.suggested_object_id is not None and (
            not isinstance(self.suggested_object_id, str)
            or not re.fullmatch(
                r"[a-z0-9]+(?:_[a-z0-9]+)*", self.suggested_object_id
            )
        ):
            raise ValueError(
                "suggested_object_id must be lowercase and contain only "
                "letters, numbers, and single underscores"
            )


@dataclass(frozen=True, slots=True)
class RegisterDefinition:
    """Describe one register relative to the start of a SunSpec model.

    ``offset`` is never a Fronius documentation register number or a pymodbus
    zero-based absolute address. It is always relative to the beginning of the
    discovered SunSpec model.
    """

    name: str
    offset: int
    size: int
    data_type: RegisterDataType
    access: RegisterAccess
    unit: str | None = None
    scale_factor: str | None = None
    description: str | None = None
    valid_range: ValueRange | None = None
    enum: Mapping[int, str] | None = None
    bitfield: Mapping[int, str] | None = None
    invalid_values: tuple[int, ...] = ()
    entity: EntityDefinition | None = None
    poll_class: PollClass = PollClass.NORMAL
    help_text: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        """Validate the definition and freeze supplied metadata mappings."""
        if not self.name.strip():
            raise ValueError("register name must not be empty")
        if self.offset < 0:
            raise ValueError("register offset must not be negative")
        if self.size <= 0:
            raise ValueError("register size must be positive")
        if self.scale_factor is not None and not self.scale_factor.strip():
            raise ValueError("scale_factor must not be empty")
        if self.enum is not None and self.data_type is not RegisterDataType.ENUM16:
            raise ValueError("enum metadata requires the enum16 data type")
        if self.bitfield is not None and self.data_type not in {
            RegisterDataType.BITFIELD16,
            RegisterDataType.BITFIELD32,
        }:
            raise ValueError("bitfield metadata requires a bitfield data type")
        if self.enum is not None:
            object.__setattr__(self, "enum", MappingProxyType(dict(self.enum)))
        if self.bitfield is not None:
            object.__setattr__(
                self, "bitfield", MappingProxyType(dict(self.bitfield))
            )
        if self.help_text is not None:
            if any(
                not isinstance(language, str) or not isinstance(text, str)
                for language, text in self.help_text.items()
            ):
                raise ValueError("help_text keys and values must be strings")
            if any(
                not language or language != language.lower()
                for language in self.help_text
            ):
                raise ValueError("help_text language codes must be non-empty lowercase")
            if "en" not in self.help_text:
                raise ValueError("help_text must contain an English 'en' entry")
            if any(not text.strip() for text in self.help_text.values()):
                raise ValueError("help_text translations must not be empty")
            object.__setattr__(
                self, "help_text", MappingProxyType(dict(self.help_text))
            )


def get_help_text(
    definition: RegisterDefinition, language: str = "en"
) -> str | None:
    """Return localized explanatory text with conservative English fallback."""
    if definition.help_text is None:
        return None
    normalized = language.replace("_", "-").lower()
    candidates = (normalized, normalized.split("-", 1)[0], "en")
    return next(
        definition.help_text[candidate]
        for candidate in candidates
        if candidate in definition.help_text
    )


def _validate_register_layout(
    registers: tuple[RegisterDefinition, ...],
    *,
    container_name: str,
    length: int | None = None,
    length_name: str | None = None,
) -> None:
    """Validate unique names, non-overlap, and optional container bounds."""
    register_names = [register.name for register in registers]
    if len(register_names) != len(set(register_names)):
        raise ValueError(f"register names must be unique within {container_name}")
    if length is not None and any(
        register.offset + register.size > length for register in registers
    ):
        raise ValueError(f"register must fit within {length_name or container_name}")

    by_offset = sorted(registers, key=lambda register: register.offset)
    for previous, current in zip(by_offset, by_offset[1:], strict=False):
        if current.offset < previous.offset + previous.size:
            raise ValueError(
                f"register ranges overlap: {previous.name!r} and {current.name!r}"
            )


@dataclass(frozen=True, slots=True)
class RepeatingBlockDefinition:
    """Describe a repeated group of registers within a SunSpec model.

    ``offset`` is the model-relative start of the first possible instance.
    Register offsets within ``registers`` are relative to each repeated block.
    The definition deliberately contains no instance count; future discovery
    determines which instances a physical device actually implements.
    """

    name: str
    offset: int
    block_size: int
    registers: tuple[RegisterDefinition, ...]

    def __post_init__(self) -> None:
        """Validate the repeating block coordinates and register layout."""
        if not self.name.strip():
            raise ValueError("repeating block name must not be empty")
        if self.offset < 0:
            raise ValueError("repeating block offset must not be negative")
        if self.block_size <= 0:
            raise ValueError("repeating block size must be positive")
        _validate_register_layout(
            self.registers,
            container_name="repeating block",
            length=self.block_size,
        )


@dataclass(frozen=True, slots=True)
class SunSpecModelDefinition:
    """A SunSpec model layout known by the integration.

    A definition describes integration knowledge. A :class:`DiscoveredModel`
    separately records what a particular physical device actually exposes.
    """

    model_ids: tuple[int, ...]
    name: str
    registers: tuple[RegisterDefinition, ...]
    expected_length: int | None = None
    repeating_blocks: tuple[RepeatingBlockDefinition, ...] = ()

    def __post_init__(self) -> None:
        """Validate model identity and its register layout."""
        if not self.model_ids:
            raise ValueError("model_ids must not be empty")
        if any(model_id < 0 for model_id in self.model_ids):
            raise ValueError("model IDs must not be negative")
        if len(self.model_ids) != len(set(self.model_ids)):
            raise ValueError("model IDs must be unique")
        if not self.name.strip():
            raise ValueError("model name must not be empty")
        if self.expected_length is not None and self.expected_length <= 0:
            raise ValueError("expected_length must be positive")

        _validate_register_layout(
            self.registers,
            container_name="model",
            length=self.expected_length,
            length_name="expected_length",
        )
        block_names = [block.name for block in self.repeating_blocks]
        if len(block_names) != len(set(block_names)):
            raise ValueError("repeating block names must be unique within a model")
        if self.expected_length is not None and any(
            block.offset + block.block_size > self.expected_length
            for block in self.repeating_blocks
        ):
            raise ValueError("repeating block must fit within expected_length")
        for register in self.registers:
            register_end = register.offset + register.size
            for block in self.repeating_blocks:
                block_end = block.offset + block.block_size
                if register.offset < block_end and block.offset < register_end:
                    raise ValueError(
                        f"fixed register {register.name!r} overlaps repeating block "
                        f"{block.name!r}"
                    )


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """A concrete SunSpec model exposed by a physical device.

    ``base_address`` is the transport-independent SunSpec model base used
    internally. Conversion to a pymodbus address is intentionally deferred to
    the future Modbus transport layer.
    """

    model_id: int
    base_address: int
    length: int

    def __post_init__(self) -> None:
        """Validate discovered model coordinates."""
        if self.model_id < 0:
            raise ValueError("model_id must not be negative")
        if self.base_address < 0:
            raise ValueError("base_address must not be negative")
        if self.length <= 0:
            raise ValueError("length must be positive")


@dataclass(frozen=True, slots=True)
class DiscoveredRepeatingBlockInstance:
    """A usable concrete instance of a repeating register block.

    ``instance_index`` is zero-based. ``base_offset`` is the model-relative
    start of this concrete instance. This type records a discovery result but
    does not decide whether any theoretically possible instance is present.
    """

    block_name: str
    instance_index: int
    base_offset: int

    def __post_init__(self) -> None:
        """Validate the concrete repeated-instance identity and coordinates."""
        if not self.block_name.strip():
            raise ValueError("repeating block name must not be empty")
        if self.instance_index < 0:
            raise ValueError("instance_index must not be negative")
        if self.base_offset < 0:
            raise ValueError("base_offset must not be negative")


@dataclass(frozen=True, slots=True)
class RegisterValue:
    """Keep a decoded value together with its transport-level raw value."""

    raw: int | str | None
    value: int | float | str | None
