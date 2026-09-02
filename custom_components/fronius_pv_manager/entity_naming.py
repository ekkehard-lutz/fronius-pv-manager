"""Stable language-independent object-ID helpers shared by entity platforms."""

from .models import EntityDefinition
from .semantics import Model160ModuleKind

_MODEL_160_NAMES = {
    "DCA": ("dc_current", "current"),
    "DCV": ("dc_voltage", "voltage"),
    "DCW": ("dc_power", "power"),
    "DCWH": ("dc_energy", "energy"),
    "Tms": ("timestamp", "timestamp"),
    "Tmp": ("temperature", "temperature"),
    "DCSt": ("operating_state", "operating_state"),
    "DCEvt": ("event_flags", "event_flags"),
}


def suggested_object_id(
    entity: EntityDefinition,
    *,
    model_160_kind: Model160ModuleKind | None = None,
    register_name: str | None = None,
    mppt_number: int | None = None,
) -> str:
    """Return the explicit catalog ID or a classified Model 160 runtime ID."""
    if model_160_kind is None:
        if entity.suggested_object_id is None:
            raise ValueError(f"entity {entity.key!r} has no suggested object ID")
        return entity.suggested_object_id

    if register_name not in _MODEL_160_NAMES:
        raise ValueError(f"unsupported Model 160 register {register_name!r}")
    mppt_semantic, storage_semantic = _MODEL_160_NAMES[register_name]
    if model_160_kind is Model160ModuleKind.MPPT:
        if mppt_number is None or mppt_number < 1:
            raise ValueError("MPPT object IDs require a one-based MPPT number")
        return f"mppt_{mppt_number}_{mppt_semantic}"
    prefix = {
        Model160ModuleKind.STORAGE_CHARGE: "charging",
        Model160ModuleKind.STORAGE_DISCHARGE: "discharging",
    }.get(model_160_kind)
    if prefix is None:
        raise ValueError("unknown Model 160 modules do not expose entities")
    return f"{prefix}_{storage_semantic}"
