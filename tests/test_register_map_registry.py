"""Tests for supported SunSpec register-map lookup."""

from custom_components.fronius_pv_manager.register_maps import (
    MODEL_1,
    MODEL_203,
    MODEL_DEFINITIONS_BY_ID,
    find_registers,
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


def test_unique_bare_register_name_lookup() -> None:
    """A semantic name occurring once resolves without model qualification."""
    matches = find_registers("ChaState")
    assert len(matches) == 1
    assert matches[0].model_id == 124
    assert matches[0].register.name == "ChaState"


def test_ambiguous_bare_register_name_returns_every_match() -> None:
    """The registry preserves ambiguity rather than selecting a preferred model."""
    assert {match.model_id for match in find_registers("W")} == {103, 203}


def test_qualified_and_repeating_register_lookup() -> None:
    """Model qualification resolves fixed and repeating-block definitions."""
    assert find_registers("W", model_id=103)[0].model_id == 103
    repeated = find_registers("DCW", model_id=160)
    assert len(repeated) == 1
    assert repeated[0].block_name == "module"


def test_unknown_model_or_register_has_no_match() -> None:
    """Unsupported lookup coordinates produce an empty immutable result."""
    assert find_registers("W", model_id=999) == ()
    assert find_registers("NotARegister", model_id=103) == ()
