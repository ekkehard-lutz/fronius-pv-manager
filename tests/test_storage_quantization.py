"""GEN24 beta.1 regression: preserve watt constraints at register resolution."""

from dataclasses import asdict, replace
from fractions import Fraction

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.number import StorageNumber, async_setup_entry
from custom_components.fronius_pv_manager.storage_control import (
    PowerSettings,
    StorageControl,
    power_window,
)
from tests.control_entity_fakes import MODEL_BASE
from tests.runtime_fakes import FakeEntry
from tests.test_storage_control import controller

REFERENCE = 10240
FIELDS = [
    ("maximum_charge_power", "InWRte", False),
    ("maximum_discharge_power", "OutWRte", False),
    ("minimum_charge_power", "OutWRte", True),
    ("minimum_discharge_power", "InWRte", True),
]


def hardware(reference=REFERENCE, sf=-2):
    coordinator, control = controller()
    transport = coordinator.control_transport
    transport.registers[MODEL_BASE] = reference
    transport.registers[MODEL_BASE + 23] = sf & 0xFFFF
    transport.registers[MODEL_BASE + 10] = 10000
    transport.registers[MODEL_BASE + 11] = 10000
    coordinator.data = coordinator._snapshot()
    return coordinator, control


async def power_entities(coordinator):
    entry = FakeEntry({})
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    return {
        entity.key: entity for entity in entities if isinstance(entity, StorageNumber)
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field,register,minimum", FIELDS)
@pytest.mark.parametrize(
    "watts,maximum_raw,minimum_raw",
    [
        (1000, 976, 977),
        (2500, 2441, 2442),
        (5000, 4882, 4883),
    ],
)
@pytest.mark.parametrize("initial_mode", ["automatic", "manual"])
async def test_ordinary_watts_apply_exact_safe_targets_and_keep_semantic_settings(
    field,
    register,
    minimum,
    watts,
    maximum_raw,
    minimum_raw,
    initial_mode,
):
    coordinator, control = hardware()
    entities = await power_entities(coordinator)
    if initial_mode == "manual":
        await control.async_change(1, mode="manual")
    before = len(coordinator.control_transport.write_calls)
    refreshes = coordinator.refresh_requests
    await entities[field].async_set_native_value(watts)
    if initial_mode == "automatic":
        assert len(coordinator.control_transport.write_calls) == before
        await control.async_change(1, mode="manual")
    assert len(coordinator.control_transport.write_calls) == before + 6
    assert coordinator.refresh_requests == refreshes + 1
    raw = -minimum_raw if minimum else maximum_raw
    expected_percent = raw / 100
    fixed = control.snapshot(1)
    assert fixed[register].raw == (raw & 0xFFFF)
    assert fixed[register].value == expected_percent
    # Verify actual signed wire words, not just a derived target.
    offset = 11 if register == "InWRte" else 10
    writes = [
        words
        for address, words in coordinator.control_transport.write_calls
        if address == MODEL_BASE + offset
    ]
    assert writes[-1] == (raw & 0xFFFF,)
    assert getattr(control.values(1), field) == watts
    assert entities[field].native_value == watts
    assert type(entities[field].native_value) is int
    assert control.last_targets["1"][register] == expected_percent
    assert control.status(1) == "manual_hlc"
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert getattr(restored.values(1), field) == watts
    assert restored.last_targets == control.last_targets
    assert restored.status(1) == "manual_hlc"
    actual_watts = Fraction(abs(raw) * REFERENCE, 10000)
    assert actual_watts >= watts if minimum else actual_watts <= watts
    if watts == 1000:
        assert actual_watts == Fraction("1000.448" if minimum else "999.424")


@pytest.mark.parametrize("sf", [-2, -1, 0, 1, 2])
@pytest.mark.parametrize("field,register,minimum", FIELDS)
@pytest.mark.parametrize("watts", [0, REFERENCE])
def test_endpoints_are_exact(sf, field, register, minimum, watts):
    settings = replace(PowerSettings(0, REFERENCE, 0, REFERENCE), **{field: watts})
    target = power_window(settings, REFERENCE, "manual", sf)
    # At zero minimum, the corresponding opposite maximum remains unrestricted.
    expected = 100 if minimum and watts == 0 else (-100 if minimum else 100)
    if not minimum and watts == 0:
        expected = 0
    assert target[register] == expected
    assert -target["InWRte"] <= target["OutWRte"]
    assert power_window(settings, REFERENCE, "automatic", sf) == {
        "StorCtl_Mod": 0,
        "InWRte": 100,
        "OutWRte": 100,
    }


@pytest.mark.parametrize(
    "sf,maximum,minimum",
    [
        (-2, 9.76, 9.77),
        (-1, 9.7, 9.8),
        (0, 9, 10),
        (1, 0, 10),
        (2, 0, 100),
    ],
)
def test_other_rate_scales(sf, maximum, minimum):
    assert (
        power_window(PowerSettings(0, 1000, 0, REFERENCE), REFERENCE, "manual", sf)[
            "InWRte"
        ]
        == maximum
    )
    assert (
        power_window(
            PowerSettings(1000, REFERENCE, 0, REFERENCE), REFERENCE, "manual", sf
        )["OutWRte"]
        == -minimum
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference,sf",
    [
        (6000, -2),
        (10000, -2),
        (10240, -2),
        (10000, -1),
        (6000, 0),
        (1024, 1),
    ],
)
async def test_ui_step_is_one_watt_independent_of_hardware_resolution(reference, sf):
    coordinator, _ = hardware(reference, sf)
    entities = await power_entities(coordinator)
    for field, _, _ in FIELDS:
        assert entities[field].native_step == 1


@pytest.mark.parametrize("sf", [None, True, -32768, 32768, -3, 3])
def test_unavailable_or_unencodable_neutral_resolution_fails_closed(sf):
    with pytest.raises(ServiceValidationError):
        power_window(PowerSettings(0, 1000, 0, 1000), REFERENCE, "manual", sf)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        PowerSettings(1001, 1000, 0, REFERENCE),
        PowerSettings(1, REFERENCE, 1, REFERENCE),
        PowerSettings(1000, 1000, 0, REFERENCE),
        PowerSettings(0, REFERENCE, 1000, 1000),
    ],
)
async def test_invalid_or_quantization_collapsed_window_rejected_before_any_write(
    settings,
):
    coordinator, control = hardware()
    control.settings["1"] = asdict(settings)
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, mode="manual")
    assert not coordinator.control_transport.write_calls
    assert not control.last_targets
    assert coordinator.refresh_requests == 0


@pytest.mark.asyncio
async def test_ui_step_stays_one_watt_when_hardware_resolution_changes():
    coordinator, control = hardware()
    entities = await power_entities(coordinator)
    for field, _, _ in FIELDS:
        assert entities[field].native_step == 1
        assert entities[field].native_max_value == REFERENCE
    assert entities["minimum_reserve"].native_step == 1
    # Every whole-watt value is valid; fractional-watt input remains rejected.
    await entities["maximum_charge_power"].async_set_native_value(1001)
    assert entities["maximum_charge_power"].native_value == 1001
    with pytest.raises(ServiceValidationError, match="whole watts"):
        await entities["maximum_charge_power"].async_set_native_value(1000.5)
    coordinator.control_transport.registers[MODEL_BASE + 23] = 0xFFFF
    coordinator.data = coordinator._snapshot()
    assert entities["maximum_charge_power"].native_step == 1
    assert entities["maximum_charge_power"].native_value == 1001
    await control.async_change(1, mode="manual")
    assert control.last_targets["1"]["InWRte"] == 9.7
    assert control.status(1) == "manual_hlc"


@pytest.mark.asyncio
async def test_live_encoder_still_rejects_changed_scale_before_first_write():
    coordinator, control = hardware()
    await control.async_change(1, field="maximum_charge_power", value=1000)
    # Snapshot says -2, but the preflight sees a coarser scale which cannot encode 9.76.
    coordinator.control_transport.registers[MODEL_BASE + 23] = 0xFFFF
    from custom_components.fronius_pv_manager.write_runtime import (
        WriteInvalidValueError,
    )

    with pytest.raises(WriteInvalidValueError):
        await control.async_change(1, mode="manual")
    assert not coordinator.control_transport.write_calls
    assert not control.last_targets


@pytest.mark.asyncio
async def test_fractional_reference_still_exposes_only_whole_watt_defaults_and_bounds():
    coordinator, control = hardware()
    coordinator.control_transport.registers[MODEL_BASE] = 10245
    coordinator.control_transport.registers[MODEL_BASE + 16] = 0xFFFF
    coordinator.data = coordinator._snapshot()
    assert control.reference(1) == 1024.5
    entities = await power_entities(coordinator)
    maximum = entities["maximum_charge_power"]
    assert maximum.native_max_value == 1024
    assert maximum.native_value == 1024
    assert maximum.native_step == 1
    await maximum.async_set_native_value(1024)
    await control.async_change(1, mode="manual")
    assert control.last_targets["1"]["InWRte"] == 99.95
    assert maximum.native_value == 1024


@pytest.mark.asyncio
async def test_missing_resolution_disables_power_ui_but_automatic_edit_stays_semantic():
    coordinator, control = hardware()
    coordinator.control_transport.registers[MODEL_BASE + 23] = 0x8000
    coordinator.data = coordinator._snapshot()
    entities = await power_entities(coordinator)
    assert not entities["maximum_charge_power"].available
    assert entities["minimum_reserve"].available
    # Domain persistence in automatic mode does not require rate representation.
    await control.async_change(1, field="maximum_charge_power", value=1000)
    assert control.values(1).maximum_charge_power == 1000
    assert not coordinator.control_transport.write_calls
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, mode="manual")
    assert not coordinator.control_transport.write_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("field,register,minimum", FIELDS)
async def test_adjacent_semantic_watts_can_share_a_hardware_target(
    field, register, minimum
):
    coordinator, control = hardware()
    entities = await power_entities(coordinator)
    await control.async_change(1, mode="manual")
    first = 1023 if minimum else 1024
    await entities[field].async_set_native_value(first)
    target = dict(control.last_targets["1"])
    assert target[register] == (-10 if minimum else 10)
    await entities[field].async_set_native_value(first + entities[field].native_step)
    assert entities[field].native_value == first + 1
    assert getattr(control.values(1), field) == first + 1
    assert control.last_targets["1"] == target
    assert control.status(1) == "manual_hlc"
