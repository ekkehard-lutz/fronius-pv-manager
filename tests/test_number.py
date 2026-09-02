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


async def number_entities(policy: WritePolicy | None):
    """Set up number entities for one optional synthetic runtime policy."""
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


def by_register(entities, name: str):
    """Return one control entity by semantic register name."""
    return next(entity for entity in entities if entity._source.register_name == name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minimum, maximum",
    [(0, 100), (5, 20)],
)
async def test_discharge_rate_number_uses_effective_policy_range(
    minimum: int, maximum: int
) -> None:
    """Hard and installation bounds become the advertised number range."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "OutWRte", minimum, maximum)
    )

    entity = by_register(entities, "OutWRte")
    assert entity._source.role is PhysicalDeviceRole.STORAGE
    assert entity.native_value == 0
    assert entity.native_min_value == minimum
    assert entity.native_max_value == maximum
    assert entity.native_step is None
    assert entity.native_unit_of_measurement == "% WChaMax"
    assert entity.entity_category is EntityCategory.CONFIG
    assert not entity.entity_description.entity_registry_enabled_default
    assert entity.unique_id == "test-entry_device1_storage_model_124_outwrte"
    assert entity.suggested_object_id == "storage_reg_maximum_discharge_rate"
    assert entity.device_info["identifiers"] == {
        ("fronius_pv_manager", "test-entry:device1:storage")
    }
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_discharge_rate_exists_without_policy_with_hard_bounds() -> None:
    """Catalog exposure and hard presentation bounds do not require policy."""
    coordinator, entities = await number_entities(None)
    entity = by_register(entities, "OutWRte")

    assert entity.native_value == 0
    assert entity.native_min_value == -100
    assert entity.native_max_value == 100
    assert not entity.entity_description.entity_registry_enabled_default
    with pytest.raises(ServiceValidationError, match="not enabled"):
        await entity.async_set_native_value(10)
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_disabled_policy_keeps_number_readable_with_hard_bounds() -> None:
    """A disabled narrowed policy does not narrow presentation or permit writes."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "OutWRte", 5, 20, enabled=False)
    )
    entity = by_register(entities, "OutWRte")

    assert entity.native_value == 0
    assert entity.native_min_value == -100
    assert entity.native_max_value == 100
    with pytest.raises(ServiceValidationError, match="not enabled"):
        await entity.async_set_native_value(10)
    assert coordinator.control_transport.write_calls == []


@pytest.mark.asyncio
async def test_enabled_policy_without_narrowing_uses_hard_bounds() -> None:
    """Omitted installation limits inherit the authoritative register range."""
    _, entities = await number_entities(WritePolicy(124, "OutWRte"))
    entity = by_register(entities, "OutWRte")

    assert entity.native_min_value == -100
    assert entity.native_max_value == 100


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

    assert all(entity._source.register_name != "VAChaMax" for entity in entities)


@pytest.mark.asyncio
async def test_minimum_reserve_is_exposed_with_hard_percentage_bounds() -> None:
    """MinRsvPct is a readable NUMBER using its project-authoritative range."""
    _, entities = await number_entities(None)
    entity = by_register(entities, "MinRsvPct")

    assert entity.native_value == 7
    assert entity.native_min_value == 0
    assert entity.native_max_value == 100
    assert entity.native_unit_of_measurement == "%"


@pytest.mark.asyncio
async def test_out_of_range_number_write_is_rejected_before_modbus() -> None:
    """Advertised bounds reject unsafe calls before the write runtime executes."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "OutWRte", 5, 20)
    )

    for value in (21, float("nan")):
        with pytest.raises(ServiceValidationError):
            await by_register(entities, "OutWRte").async_set_native_value(value)

    assert coordinator.control_transport.write_calls == []
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_successful_number_write_uses_verified_runtime_and_refresh() -> None:
    """A number write executes once and publishes only refreshed device state."""
    coordinator, entities = await number_entities(
        WritePolicy(124, "OutWRte", -100, 100)
    )
    entity = by_register(entities, "OutWRte")

    await entity.async_set_native_value(10)

    assert coordinator.control_transport.write_calls == [
        (MODEL_BASE + register("OutWRte").offset, (10,))
    ]
    assert coordinator.refresh_requests == 1
    assert entity.native_value == 10


@pytest.mark.asyncio
async def test_failed_number_verification_keeps_confirmed_state() -> None:
    """A mismatch neither refreshes nor publishes the requested value."""
    policy = WritePolicy(124, "OutWRte", -100, 100)
    coordinator = ControlCoordinator(
        {(124, "OutWRte"): policy}, transport=FailingReadBackTransport()
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(
        coordinator.hass, entry, lambda items: entities.extend(items)
    )

    with pytest.raises(WriteVerificationMismatchError):
        await by_register(entities, "OutWRte").async_set_native_value(10)

    assert len(coordinator.control_transport.write_calls) == 1
    assert coordinator.refresh_requests == 0
    assert by_register(entities, "OutWRte").native_value == 0


@pytest.mark.asyncio
async def test_writable_number_has_no_duplicate_sensor() -> None:
    """Catalog platform selection creates one HA representation per register."""
    coordinator, numbers = await number_entities(
        WritePolicy(124, "OutWRte", -100, 100)
    )
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    sensors = []
    await async_setup_sensors(
        coordinator.hass, entry, lambda items: sensors.extend(items)
    )

    assert by_register(numbers, "OutWRte")
    assert all(sensor._source.register_name != "OutWRte" for sensor in sensors)


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


def test_low_level_control_names_have_localized_register_prefix() -> None:
    """Control display names are distinct from future high-level entities."""
    root = Path(__file__).parents[1] / "custom_components" / "fronius_pv_manager"
    english = json.loads(
        (root / "translations/en.json").read_text(encoding="utf-8")
    )["entity"]
    german = json.loads(
        (root / "translations/de.json").read_text(encoding="utf-8")
    )["entity"]

    for platform in ("number", "select"):
        assert all(
            item["name"].startswith("Register ")
            for item in english[platform].values()
        )
        assert all(
            item["name"].startswith("Register ")
            for item in german[platform].values()
        )
    assert english["number"]["model_124_minrsvpct"]["name"] == (
        "Register minimum storage reserve"
    )
    assert german["number"]["model_124_minrsvpct"]["name"] == (
        "Register Mindestspeicherreserve"
    )
