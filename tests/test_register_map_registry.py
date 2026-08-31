"""Tests for supported SunSpec register-map lookup."""

from custom_components.fronius_pv_manager.register_maps import (
    MODEL_1,
    MODEL_203,
    MODEL_DEFINITIONS_BY_ID,
    get_model_definition,
)


def test_supported_model_ids_resolve_to_existing_definitions() -> None:
    """Every currently reviewed model is available through one registry."""
    assert set(MODEL_DEFINITIONS_BY_ID) == {1, 103, 120, 121, 122, 123, 124, 160, 203}
    assert get_model_definition(1) is MODEL_1
    assert get_model_definition(203) is MODEL_203


def test_unknown_model_id_has_no_local_definition() -> None:
    """Unknown IDs remain valid discovery results without a local map."""
    assert get_model_definition(999) is None
