"""Tests for stable Home Assistant-independent entity object-ID resolution."""

import pytest

from custom_components.fronius_pv_manager.entity_naming import suggested_object_id
from custom_components.fronius_pv_manager.models import EntityDefinition, EntityPlatform
from custom_components.fronius_pv_manager.semantics import Model160ModuleKind


def _entity(suggested: str | None = None) -> EntityDefinition:
    return EntityDefinition(
        EntityPlatform.SENSOR,
        "model_160_module_test",
        suggested_object_id=suggested,
    )


def test_fixed_entity_requires_explicit_semantic_id() -> None:
    """Missing fixed metadata fails instead of deriving a translated fallback."""
    with pytest.raises(ValueError, match="no suggested object ID"):
        suggested_object_id(_entity())
    assert suggested_object_id(_entity("ac_power")) == "ac_power"


@pytest.mark.parametrize(
    "register_name, mppt, charging, discharging",
    (
        ("DCA", "mppt_2_dc_current", "charging_current", "discharging_current"),
        ("DCV", "mppt_2_dc_voltage", "charging_voltage", "discharging_voltage"),
        ("DCW", "mppt_2_dc_power", "charging_power", "discharging_power"),
        ("DCWH", "mppt_2_dc_energy", "charging_energy", "discharging_energy"),
        ("Tms", "mppt_2_timestamp", "charging_timestamp", "discharging_timestamp"),
        (
            "Tmp",
            "mppt_2_temperature",
            "charging_temperature",
            "discharging_temperature",
        ),
        (
            "DCSt",
            "mppt_2_operating_state",
            "charging_operating_state",
            "discharging_operating_state",
        ),
        (
            "DCEvt",
            "mppt_2_event_flags",
            "charging_event_flags",
            "discharging_event_flags",
        ),
    ),
)
def test_model_160_runtime_semantic_names(
    register_name: str,
    mppt: str,
    charging: str,
    discharging: str,
) -> None:
    """Runtime classification selects exact MPPT and storage-direction IDs."""
    entity = _entity()
    assert suggested_object_id(
        entity,
        model_160_kind=Model160ModuleKind.MPPT,
        register_name=register_name,
        mppt_number=2,
    ) == mppt
    assert suggested_object_id(
        entity,
        model_160_kind=Model160ModuleKind.STORAGE_CHARGE,
        register_name=register_name,
    ) == charging
    assert suggested_object_id(
        entity,
        model_160_kind=Model160ModuleKind.STORAGE_DISCHARGE,
        register_name=register_name,
    ) == discharging


def test_invalid_model_160_context_fails_closed() -> None:
    """Incomplete or unsupported runtime semantics never create unstable IDs."""
    with pytest.raises(ValueError, match="one-based MPPT number"):
        suggested_object_id(
            _entity(),
            model_160_kind=Model160ModuleKind.MPPT,
            register_name="DCA",
        )
    with pytest.raises(ValueError, match="unknown Model 160"):
        suggested_object_id(
            _entity(),
            model_160_kind=Model160ModuleKind.UNKNOWN,
            register_name="DCA",
        )
    with pytest.raises(ValueError, match="unsupported Model 160 register"):
        suggested_object_id(
            _entity(),
            model_160_kind=Model160ModuleKind.STORAGE_CHARGE,
            register_name="IDStr",
        )
