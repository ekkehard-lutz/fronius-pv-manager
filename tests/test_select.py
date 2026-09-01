"""Tests for policy-approved Home Assistant select entities."""

import json
from pathlib import Path

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.models import PhysicalDeviceRole
from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.select import async_setup_entry
from custom_components.fronius_pv_manager.write_policy import WritePolicy
from custom_components.fronius_pv_manager.write_policy_loader import (
    DEFAULT_POLICY_PATH,
    load_write_policy_text,
)
from custom_components.fronius_pv_manager.write_runtime import (
    WriteVerificationMismatchError,
)
from tests.control_entity_fakes import (
    ControlCoordinator,
    FailingReadBackTransport,
)
from tests.runtime_fakes import FakeEntry

PV = "0"
GRID = "1"
PV_LABEL = "PV (Charging from grid disabled)"
GRID_LABEL = "GRID (Charging from grid enabled)"


async def select_entities(policy: WritePolicy | None):
    """Set up select entities for one optional synthetic runtime policy."""
    policies = (
        {} if policy is None else {(policy.model_id, policy.register_name): policy}
    )
    coordinator = ControlCoordinator(policies)
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )
    return coordinator, entities


@pytest.mark.asyncio
async def test_default_policy_exposes_one_storage_select() -> None:
    """The shipped ChaGriSet approval creates its cataloged storage control."""
    policies = load_write_policy_text(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    coordinator = ControlCoordinator(policies)
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []

    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    assert len(entities) == 1
    entity = entities[0]
    assert entity._source.register_name == "ChaGriSet"
    assert entity._source.role is PhysicalDeviceRole.STORAGE
    assert entity.entity_description.entity_category is EntityCategory.CONFIG
    assert not entity.entity_description.entity_registry_enabled_default
    assert entity.options == [PV, GRID]
    assert entity.current_option == PV
    assert entity.suggested_object_id == "storage_reg_grid_charging"
    assert entity.device_info["identifiers"] == {
        ("fronius_pv_manager", "test-entry:device1:storage")
    }


@pytest.mark.asyncio
async def test_missing_policy_keeps_select_readable_but_rejects_writes() -> None:
    """Catalog exposure survives absent write permission."""
    coordinator, entities = await select_entities(None)

    assert len(entities) == 1
    assert entities[0].options == [PV, GRID]
    assert entities[0].current_option == PV
    assert not entities[0].entity_description.entity_registry_enabled_default
    with pytest.raises(ServiceValidationError, match="not enabled"):
        await entities[0].async_select_option(GRID)
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_disabled_policy_keeps_all_options_and_rejects_writes() -> None:
    """Disabled enum narrowing is retained but ignored for presentation."""
    coordinator, entities = await select_entities(
        WritePolicy(
            124,
            "ChaGriSet",
            allowed_enum_values=frozenset({1}),
            enabled=False,
        )
    )

    assert len(entities) == 1
    assert entities[0].options == [PV, GRID]
    assert entities[0].current_option == PV
    with pytest.raises(ServiceValidationError, match="not enabled"):
        await entities[0].async_select_option(GRID)
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_select_options_come_from_documented_enum_and_policy_subset() -> None:
    """An enum subset narrows stable keys backed by authoritative raw values."""
    _, full = await select_entities(WritePolicy(124, "ChaGriSet"))
    _, narrowed = await select_entities(
        WritePolicy(124, "ChaGriSet", allowed_enum_values=frozenset({1}))
    )

    assert full[0].options == [PV, GRID]
    assert full[0].current_option == PV
    assert narrowed[0].options == [GRID]
    assert narrowed[0].current_option is None


@pytest.mark.asyncio
async def test_invalid_select_option_is_rejected_without_modbus() -> None:
    """Only an option present in the active policy can reach the runtime."""
    coordinator, entities = await select_entities(
        WritePolicy(124, "ChaGriSet", allowed_enum_values=frozenset({1}))
    )

    with pytest.raises(ServiceValidationError):
        await entities[0].async_select_option(PV)

    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_successful_selection_uses_verified_write_and_refresh() -> None:
    """Documented option maps to raw enum and follows the existing write path."""
    coordinator, entities = await select_entities(WritePolicy(124, "ChaGriSet"))
    entity = entities[0]

    await entity.async_select_option(GRID)

    assert coordinator.control_transport.write_calls == [(41015, (1,))]
    assert coordinator.refresh_requests == 1
    assert entity.current_option == GRID


@pytest.mark.asyncio
async def test_pv_selection_writes_authoritative_raw_zero() -> None:
    """The translated PV UI option key resolves to documented raw value zero."""
    coordinator, entities = await select_entities(WritePolicy(124, "ChaGriSet"))

    await entities[0].async_select_option(PV)

    assert coordinator.control_transport.write_calls == [(41015, (0,))]
    assert coordinator.refresh_requests == 1
    assert entities[0].current_option == PV


@pytest.mark.asyncio
async def test_verification_failure_does_not_publish_requested_option() -> None:
    """A failed readback retains the last coordinator-confirmed select state."""
    policy = WritePolicy(
        124, "ChaGriSet", allowed_enum_values=frozenset({0, 1})
    )
    coordinator = ControlCoordinator(
        {(124, "ChaGriSet"): policy}, transport=FailingReadBackTransport()
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )
    entity = entities[0]

    with pytest.raises(WriteVerificationMismatchError):
        await entity.async_select_option(GRID)

    assert coordinator.control_transport.write_calls == [(41015, (1,))]
    assert coordinator.refresh_requests == 0
    assert entity.current_option == PV


def test_select_option_translations_match_stable_keys_and_enum_semantics() -> None:
    """English and German resources localize the two stable enum option keys."""
    root = Path(__file__).parents[1] / "custom_components" / "fronius_pv_manager"
    resources = {
        language: json.loads((root / path).read_text(encoding="utf-8"))
        for language, path in (
            ("source", "strings.json"),
            ("en", "translations/en.json"),
            ("de", "translations/de.json"),
        )
    }
    english = {"0": PV_LABEL, "1": GRID_LABEL}
    german = {
        "0": "PV (Netzladung deaktiviert)",
        "1": "GRID (Netzladung aktiviert)",
    }

    assert resources["source"]["entity"]["select"]["model_124_chagriset"][
        "state"
    ] == english
    assert resources["en"]["entity"]["select"]["model_124_chagriset"][
        "state"
    ] == english
    assert resources["de"]["entity"]["select"]["model_124_chagriset"][
        "state"
    ] == german
    register = next(item for item in MODEL_124.registers if item.name == "ChaGriSet")
    assert register.enum == {0: PV_LABEL, 1: GRID_LABEL}
