"""Reviewed SunSpec register-map definitions shipped by the integration."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..models import RegisterDefinition, SunSpecModelDefinition
from .model_1 import MODEL_1
from .model_103 import MODEL_103
from .model_120 import MODEL_120
from .model_121 import MODEL_121
from .model_122 import MODEL_122
from .model_123 import MODEL_123
from .model_124 import MODEL_124
from .model_160 import MODEL_160
from .model_203 import MODEL_203

_DEFINITIONS = (
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

MODEL_DEFINITIONS_BY_ID: Mapping[int, SunSpecModelDefinition] = MappingProxyType(
    {
        model_id: definition
        for definition in _DEFINITIONS
        for model_id in definition.model_ids
    }
)


def get_model_definition(model_id: int) -> SunSpecModelDefinition | None:
    """Return the local definition for a supported SunSpec model ID."""
    return MODEL_DEFINITIONS_BY_ID.get(model_id)


@dataclass(frozen=True, slots=True)
class RegisterLookup:
    """Identify one fixed or repeating register in the local map registry."""

    model_id: int
    model: SunSpecModelDefinition
    register: RegisterDefinition
    block_name: str | None = None


def find_registers(
    name: str, *, model_id: int | None = None
) -> tuple[RegisterLookup, ...]:
    """Find exact, case-sensitive register-name matches in known definitions."""
    definitions = (
        ()
        if model_id is not None and model_id not in MODEL_DEFINITIONS_BY_ID
        else (
            (MODEL_DEFINITIONS_BY_ID[model_id],)
            if model_id is not None
            else tuple(MODEL_DEFINITIONS_BY_ID.values())
        )
    )
    matches = []
    for model in definitions:
        current_model_id = model_id if model_id is not None else model.model_ids[0]
        matches.extend(
            RegisterLookup(current_model_id, model, register)
            for register in model.registers
            if register.name == name
        )
        for block in model.repeating_blocks:
            matches.extend(
                RegisterLookup(current_model_id, model, register, block.name)
                for register in block.registers
                if register.name == name
            )
    return tuple(matches)

__all__ = [
    "MODEL_1",
    "MODEL_103",
    "MODEL_120",
    "MODEL_121",
    "MODEL_122",
    "MODEL_123",
    "MODEL_124",
    "MODEL_160",
    "MODEL_203",
    "MODEL_DEFINITIONS_BY_ID",
    "RegisterLookup",
    "find_registers",
    "get_model_definition",
]
