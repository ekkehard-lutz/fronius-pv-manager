"""Tests for strict persistent YAML write-policy loading."""

from pathlib import Path

import pytest
import yaml

from custom_components.fronius_pv_manager.write_policy_loader import (
    DEFAULT_POLICY_PATH,
    WritePolicyLoadError,
    load_or_create_write_policy,
    load_write_policy_text,
)


def policy_yaml(body: str, *, model_id: int = 124) -> str:
    """Wrap register YAML in the supported versioned schema."""
    return f"version: 1\nmodels:\n  {model_id}:\n{body}"


def test_shipped_default_contains_exact_intended_approvals() -> None:
    """The package default lists every writable register explicitly."""
    policies = load_write_policy_text(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))

    assert len(policies) == 25
    assert sum(model_id == 123 for model_id, _ in policies) == 18
    assert sum(model_id == 124 for model_id, _ in policies) == 7
    assert {
        coordinate for coordinate, policy in policies.items() if policy.enabled
    } == {(124, "MinRsvPct"), (124, "ChaGriSet")}
    assert all(
        policy.minimum is None
        and policy.maximum is None
        and policy.step is None
        and policy.allowed_enum_values is None
        and policy.allowed_bit_mask is None
        for policy in policies.values()
    )
    reserve = policies[(124, "MinRsvPct")]
    assert reserve.enabled
    charging_source = policies[(124, "ChaGriSet")]
    assert charging_source.enabled
    document = yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    entries = [
        settings
        for registers in document["models"].values()
        for settings in registers.values()
    ]
    assert all(set(settings) == {"enabled"} for settings in entries)
    assert not document["models"][123]["WMaxLimPct_RmpTms"]["enabled"]
    assert not document["models"][124]["VAChaMax"]["enabled"]


def test_first_load_creates_installation_policy_from_default(tmp_path: Path) -> None:
    """A missing installation file is created once outside the integration."""
    path, policies = load_or_create_write_policy(tmp_path)

    assert path == tmp_path / "fronius_pv_manager" / "write_policy.yaml"
    assert path.read_text(encoding="utf-8") == DEFAULT_POLICY_PATH.read_text(
        encoding="utf-8"
    )
    assert len(policies) == 25


def test_existing_policy_is_not_overwritten_and_reload_rereads_it(
    tmp_path: Path,
) -> None:
    """Operator policy persists and changed contents apply only on reload."""
    path = tmp_path / "fronius_pv_manager" / "write_policy.yaml"
    path.parent.mkdir()
    first = policy_yaml("    MinRsvPct:\n      minimum: 5\n      maximum: 95\n")
    path.write_text(first, encoding="utf-8")

    _, snapshot = load_or_create_write_policy(tmp_path)
    assert path.read_text(encoding="utf-8") == first
    assert snapshot[(124, "MinRsvPct")].minimum == 5
    path.write_text("version: 1\nmodels: {}\n", encoding="utf-8")

    assert (124, "MinRsvPct") in snapshot
    _, reloaded = load_or_create_write_policy(tmp_path)
    assert not reloaded


def test_valid_narrower_numeric_range_is_accepted() -> None:
    """YAML may narrow a documented protocol range."""
    policies = load_write_policy_text(
        policy_yaml("    OutWRte:\n      minimum: -50\n      maximum: 75\n")
    )
    assert policies[(124, "OutWRte")].minimum == -50
    assert policies[(124, "OutWRte")].maximum == 75


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_enabled_boolean_is_preserved(enabled: bool) -> None:
    """Policy write authorization accepts strict YAML booleans."""
    policies = load_write_policy_text(
        policy_yaml(
            "    MinRsvPct:\n"
            f"      enabled: {str(enabled).lower()}\n"
            "      minimum: 5\n"
            "      maximum: 20\n"
        )
    )
    policy = policies[(124, "MinRsvPct")]
    assert policy.enabled is enabled
    assert policy.minimum == 5
    assert policy.maximum == 20


@pytest.mark.parametrize(
    "rendered", ["0", "1", "'true'", "'false'", "null", "[]", "{}"]
)
def test_enabled_rejects_non_boolean_yaml_values(rendered: str) -> None:
    """No YAML scalar or collection may be coerced into write permission."""
    with pytest.raises(WritePolicyLoadError, match="enabled must be a boolean"):
        load_write_policy_text(
            policy_yaml(f"    MinRsvPct:\n      enabled: {rendered}\n")
        )


def test_disabled_policy_still_undergoes_semantic_validation() -> None:
    """Disabling an entry cannot preserve unsafe or broadened constraints."""
    with pytest.raises(WritePolicyLoadError, match="broadens"):
        load_write_policy_text(
            policy_yaml(
                "    OutWRte:\n"
                "      enabled: false\n"
                "      minimum: -101\n"
                "      maximum: 20\n"
            )
        )


@pytest.mark.parametrize(
    "minimum, maximum",
    [(0, 100), (5, 20)],
)
def test_minimum_reserve_policy_can_match_or_narrow_hard_range(
    minimum: int, maximum: int
) -> None:
    """Installation bounds may match or narrow the authoritative hard range."""
    policies = load_write_policy_text(
        policy_yaml(
            "    MinRsvPct:\n"
            f"      minimum: {minimum}\n"
            f"      maximum: {maximum}\n"
        )
    )
    assert policies[(124, "MinRsvPct")].minimum == minimum
    assert policies[(124, "MinRsvPct")].maximum == maximum


@pytest.mark.parametrize(
    "minimum, maximum, message",
    [
        (-1, 100, "broadens the register minimum"),
        (0, 101, "broadens the register maximum"),
    ],
)
def test_minimum_reserve_policy_cannot_broaden_hard_range(
    minimum: int, maximum: int, message: str
) -> None:
    """Installation policy cannot exceed MinRsvPct hard bounds."""
    with pytest.raises(WritePolicyLoadError, match=message):
        load_write_policy_text(
            policy_yaml(
                "    MinRsvPct:\n"
                f"      minimum: {minimum}\n"
                f"      maximum: {maximum}\n"
            )
        )


@pytest.mark.parametrize(
    "body, message",
    [
        ("    OutWRte:\n      minimum: -101\n", "broadens the register minimum"),
        ("    OutWRte:\n      maximum: 101\n", "broadens the register maximum"),
        (
            "    MinRsvPct:\n      minimum: 10\n      maximum: 5\n",
            "minimum must not be greater",
        ),
        ("    Missing: {}\n", "unknown register"),
        ("    ChaState: {}\n", "read-only"),
    ],
)
def test_invalid_register_policies_reject_the_entire_file(
    body: str, message: str
) -> None:
    """No invalid semantic entry can be partially installed."""
    with pytest.raises(WritePolicyLoadError, match=message):
        load_write_policy_text(policy_yaml(body))


def test_unknown_model_and_repeating_register_are_rejected() -> None:
    """Policies require known fixed writable semantic coordinates."""
    with pytest.raises(WritePolicyLoadError, match="unknown model"):
        load_write_policy_text(policy_yaml("    Value: {}\n", model_id=999))
    with pytest.raises(WritePolicyLoadError, match="repeating-block"):
        load_write_policy_text(policy_yaml("    DCW: {}\n", model_id=160))


@pytest.mark.parametrize(
    "content, message",
    [
        ("version: [", "invalid YAML"),
        ("version: 2\nmodels: {}\n", "unsupported policy version"),
        ("version: 1\nmodels: {}\nextra: true\n", "unexpected keys"),
        ("version: 1\nmodels: []\n", "models must be a mapping"),
        ("version: 1\nmodels:\n  '124': {}\n", "model IDs must be integers"),
        (policy_yaml("    MinRsvPct: []\n"), "must be a mapping"),
        (policy_yaml("    MinRsvPct:\n      minimum: '0'\n"), "finite number"),
        (policy_yaml("    MinRsvPct:\n      minimum: true\n"), "finite number"),
        (policy_yaml("    MinRsvPct:\n      unexpected: 1\n"), "unexpected keys"),
        (
            "version: 1\nversion: 1\nmodels: {}\n",
            "duplicate YAML key",
        ),
    ],
)
def test_yaml_schema_and_types_are_strict(content: str, message: str) -> None:
    """Malformed, ambiguous, or loosely typed YAML is rejected."""
    with pytest.raises(WritePolicyLoadError, match=message):
        load_write_policy_text(content)


def test_valid_enum_subset_resolves_labels_and_numeric_values() -> None:
    """ENUM16 YAML choices resolve only exact documented semantics."""
    label = "PV (Charging from grid disabled)"
    policies = load_write_policy_text(
        policy_yaml(f"    ChaGriSet:\n      values:\n        - {label}\n")
    )
    assert policies[(124, "ChaGriSet")].allowed_enum_values == frozenset({0})
    numeric = load_write_policy_text(
        policy_yaml("    ChaGriSet:\n      values: [1]\n")
    )
    assert numeric[(124, "ChaGriSet")].allowed_enum_values == frozenset({1})


def test_empty_enum_subset_is_rejected_instead_of_hiding_catalog_select() -> None:
    """An empty approval cannot create an unreadable Home Assistant select."""
    with pytest.raises(WritePolicyLoadError, match="must not be empty"):
        load_write_policy_text(policy_yaml("    ChaGriSet:\n      values: []\n"))


@pytest.mark.parametrize("value", ["UNKNOWN", 2, True])
def test_unknown_enum_labels_and_values_are_rejected(value) -> None:
    """YAML enum policy cannot broaden documented membership."""
    rendered = str(value).lower() if isinstance(value, bool) else repr(value)
    with pytest.raises(WritePolicyLoadError, match="unknown enum"):
        load_write_policy_text(
            policy_yaml(f"    ChaGriSet:\n      values: [{rendered}]\n")
        )


def test_valid_bit_subset_resolves_labels_and_numeric_masks() -> None:
    """Bitfield YAML combines only exact documented masks."""
    policies = load_write_policy_text(
        policy_yaml("    StorCtl_Mod:\n      bits: [CHARGE]\n")
    )
    assert policies[(124, "StorCtl_Mod")].allowed_bit_mask == 1
    both = load_write_policy_text(
        policy_yaml("    StorCtl_Mod:\n      bits: [1, DISCHARGE]\n")
    )
    assert both[(124, "StorCtl_Mod")].allowed_bit_mask == 3


@pytest.mark.parametrize("value", ["UNKNOWN", 4, True])
def test_unknown_bit_labels_and_masks_are_rejected(value) -> None:
    """YAML bit policy cannot introduce undocumented or boolean masks."""
    rendered = str(value).lower() if isinstance(value, bool) else repr(value)
    with pytest.raises(WritePolicyLoadError, match="unknown bit"):
        load_write_policy_text(
            policy_yaml(f"    StorCtl_Mod:\n      bits: [{rendered}]\n")
        )


def test_runtime_policy_mapping_is_immutable() -> None:
    """A completely validated policy collection cannot change in place."""
    policies = load_write_policy_text(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    with pytest.raises(TypeError):
        policies[(124, "MinRsvPct")] = policies[(124, "MinRsvPct")]  # type: ignore[index]
