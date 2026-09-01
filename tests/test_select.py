"""Tests for policy-approved Home Assistant select entities."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.select import async_setup_entry
from custom_components.fronius_pv_manager.write_policy import WritePolicy
from tests.control_entity_fakes import ControlCoordinator
from tests.runtime_fakes import FakeEntry

PV = "PV (Charging from grid disabled)"
GRID = "GRID (Charging from grid enabled)"


async def select_entities(policy: WritePolicy):
    """Set up select entities for one synthetic runtime policy."""
    coordinator = ControlCoordinator(
        {(policy.model_id, policy.register_name): policy}
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )
    return coordinator, entities


@pytest.mark.asyncio
async def test_default_policy_exposes_no_select() -> None:
    """ChaGriSet remains absent because the shipped policy does not approve it."""
    coordinator = ControlCoordinator(
        {(124, "MinRsvPct"): WritePolicy(124, "MinRsvPct", 0, 100)}
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []

    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    assert entities == []


@pytest.mark.asyncio
async def test_empty_enum_subset_exposes_no_select() -> None:
    """A policy approving no enum values cannot create an invalid HA select."""
    coordinator, entities = await select_entities(
        WritePolicy(124, "ChaGriSet", allowed_enum_values=frozenset())
    )

    assert entities == []
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_select_options_come_from_documented_enum_and_policy_subset() -> None:
    """An explicit enum subset narrows authoritative register options."""
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
