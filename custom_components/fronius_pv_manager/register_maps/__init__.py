"""Reviewed SunSpec register-map definitions shipped by the integration."""

from collections.abc import Mapping
from types import MappingProxyType

from ..models import SunSpecModelDefinition
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
    "get_model_definition",
]
