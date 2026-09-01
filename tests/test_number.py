"""Tests for policy-approved Home Assistant number entities."""

import json
from pathlib import Path

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.models import (
    EntityPlatform,
    PhysicalDeviceRole,
)
from custom_components.fronius_pv_manager.number import async_setup_entry
from custom_components.fronius_pv_manager.register_maps import MODEL_DEFINITIONS_BY_ID
from custom_components.fronius_pv_manager.sensor import (
    async_setup_entry as async_setup_sensors,
)
from custom_components.fronius_pv_manager.write_policy import WritePolicy
from custom_components.fronius_pv_manager.write_runtime import (
    WriteVerificationMismatchError,
)
from tests.control_entity_fakes import (
    MODEL_BASE,
    ControlCoordinator,
    FailingReadBackTransport,
    register,
)
from tests.runtime_fakes import FakeEntry


async def number_entities(policy: WritePolicy):
    """Set up number entities for one synthetic runtime policy."""
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
@pytest.mark.parametrize(
    "minimum, maximum",
    [(0, 100), (5, 20)],
)
async def test_minimum_reserve_number_uses_effective_policy_range(
    minimum: int, maximum: int
) -> None:
    """Hard and installation bounds become the advertised number range."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "MinRsvPct", minimum, maximum)
    )

    assert len(entities) == 1
    entity = entities[0]
    assert entity._source.role is PhysicalDeviceRole.STORAGE
    assert entity.native_value == 7
    assert entity.native_min_value == minimum
    assert entity.native_max_value == maximum
    assert entity.native_step is None
    assert entity.native_unit_of_measurement == "%"
    assert entity.entity_category is EntityCategory.CONFIG
    assert not entity.entity_description.entity_registry_enabled_default
    assert entity.unique_id == "test-entry_device1_storage_model_124_minrsvpct"
    assert entity.device_info["identifiers"] == {
        ("fronius_pv_manager", "test-entry:device1:storage")
    }
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_unapproved_numbers_are_not_exposed() -> None:
    """Catalog membership alone never exposes an unrestricted control."""
    coordinator = ControlCoordinator({})
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []

    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    assert entities == []


@pytest.mark.asyncio
async def test_number_without_finite_effective_bounds_is_not_exposed() -> None:
    """HA controls fail closed when neither hard nor policy bounds are complete."""
    coordinator = ControlCoordinator(
        {(124, "VAChaMax"): WritePolicy(124, "VAChaMax")}
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []

    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    assert entities == []


@pytest.mark.asyncio
async def test_out_of_range_number_write_is_rejected_before_modbus() -> None:
    """Advertised bounds reject unsafe calls before the write runtime executes."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "MinRsvPct", 5, 20)
    )

    for value in (21, float("nan")):
        with pytest.raises(ServiceValidationError):
            await entities[0].async_set_native_value(value)

    assert coordinator.control_transport.write_calls == []
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_successful_number_write_uses_verified_runtime_and_refresh() -> None:
    """A number write executes once and publishes only refreshed device state."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "MinRsvPct", 0, 100)
    )
    entity = entities[0]

    await entity.async_set_native_value(10)

    assert coordinator.control_transport.write_calls == [
        (MODEL_BASE + register("MinRsvPct").offset, (1000,))
    ]
    assert coordinator.refresh_requests == 1
    assert entity.native_value == 10


@pytest.mark.asyncio
async def test_failed_number_verification_keeps_confirmed_state() -> None:
    """A mismatch neither refreshes nor publishes the requested value."""
    policy = WritePolicy(124, "MinRsvPct", 0, 100)
    coordinator = ControlCoordinator(
        {(124, "MinRsvPct"): policy}, transport=FailingReadBackTransport()
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    with pytest.raises(WriteVerificationMismatchError):
        await entities[0].async_set_native_value(10)

    assert len(coordinator.control_transport.write_calls) == 1
    assert coordinator.refresh_requests == 0
    assert entities[0].native_value == 7


@pytest.mark.asyncio
async def test_writable_number_has_no_duplicate_sensor() -> None:
    """Catalog platform selection creates one HA representation per register."""
    coordinator, numbers = await number_entities(
        WritePolicy(124, "MinRsvPct", 0, 100)
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    sensors = []
    await async_setup_sensors(
        coordinator.hass, entry, lambda items: sensors.extend(items)
    )

    assert len(numbers) == 1
    assert all(sensor._source.register_name != "MinRsvPct" for sensor in sensors)


def test_control_translation_keys_cover_catalog_platforms() -> None:
    """English and German resources cover every catalog number and select key."""
    root = Path(__file__).parents[1] / "custom_components" / "fronius_pv_manager"
    documents = [
        json.loads((root / path).read_text(encoding="utf-8"))["entity"]
        for path in ("strings.json", "translations/en.json", "translations/de.json")
    ]
    for platform in (EntityPlatform.NUMBER, EntityPlatform.SELECT):
        expected = {
            register.entity.translation_key
            for model in MODEL_DEFINITIONS_BY_ID.values()
            for register in model.registers
            if register.entity is not None
            and register.entity.platform is platform
        }
        assert all(set(document[platform.value]) == expected for document in documents)
