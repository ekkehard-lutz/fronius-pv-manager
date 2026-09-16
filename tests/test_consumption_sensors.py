"""Site consumption uses inverter AC output, including battery discharge."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinatorData
from custom_components.fronius_pv_manager.register_maps import MODEL_103, MODEL_203
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    SolarConsumptionSensor,
    SolarPowerSensor,
    async_setup_entry,
)
from tests.test_power_sensors import mppts
from tests.test_sensor import _coordinator_with, _snapshot

KEYS = ("consumption_power", "autarky", "self_consumption")


def power_snapshot(definition, power):
    snapshot = _snapshot(definition)
    fixed = dict(snapshot.decoded.fixed)
    fixed["W"] = replace(fixed["W"], value=power)
    return replace(snapshot, decoded=replace(snapshot.decoded, fixed=fixed))


async def site(ac=1000, grid=-400):
    coordinator, entry, transport = _coordinator_with(
        power_snapshot(MODEL_103, ac), mppts([("MPPT 1", 50)])
    )
    coordinator = type(coordinator)(
        coordinator.hass, entry, {1: transport, 200: transport}
    )
    coordinator.last_update_success = True
    entry.runtime_data = coordinator
    inverter, _, _ = _coordinator_with(
        power_snapshot(MODEL_103, ac), mppts([("MPPT 1", 50)])
    )
    meter, _, _ = _coordinator_with(power_snapshot(MODEL_203, grid))
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            inverter.data.devices[0],
            replace(meter.data.devices[0], device_id=200),
        )
    )
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    derived = {e.key: e for e in entities if isinstance(e, SolarConsumptionSensor)}
    return coordinator, entities, derived


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ac", "grid", "expected"),
    [
        (1000, 0, (1000, 100, 100)),
        (1000, 500, (1500, 100 * 2 / 3, 100)),
        (1000, -400, (600, 100, 60)),
        (4505, 6329, (10834, 100 * 4505 / 10834, 100)),
        (991.4, -679.1, (312.3, 100, 100 * 312.3 / 991.4)),
        (100, -200, (0, None, 0)),
        (0, 1000, (1000, 0, None)),
        (-100, 1000, (900, 0, None)),
        (0, 0, (0, None, None)),
        (-100, 0, (0, None, None)),
    ],
)
async def test_defined_formulas(ac, grid, expected):
    _, entities, derived = await site(ac, grid)
    assert set(derived) == set(KEYS)
    for key, value in zip(KEYS, expected, strict=True):
        entity = derived[key]
        assert entity.available == (value is not None)
        assert entity.native_value == (
            pytest.approx(value) if value is not None else None
        )
    # Regression: a deliberately unrelated PV value cannot drive these formulas.
    old = {e.key: e for e in entities if isinstance(e, SolarPowerSensor)}
    assert old["pv_power"].native_value == 50
    assert old["grid_import_power"].native_value == max(grid, 0)
    assert old["grid_export_power"].native_value == max(-grid, 0)
    raw = [
        e
        for e in entities
        if isinstance(e, FroniusPVSensor) and e._source.register_name == "W"
    ]
    assert {e._source.device_id: e.native_value for e in raw} == {1: ac, 200: grid}
    assert {e.unique_id for e in raw} == {
        "test-entry_device1_inverter_model_103_w",
        "test-entry_device200_meter_model_203_w",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [1, 200])
@pytest.mark.parametrize(
    "invalid", [None, float("nan"), float("inf"), -float("inf"), "bad", True]
)
async def test_invalid_required_power(source, invalid):
    _, _, derived = await site(
        invalid if source == 1 else 1000, invalid if source == 200 else 0
    )
    assert all(e.native_value is None and not e.available for e in derived.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [1, 200])
@pytest.mark.parametrize(
    "failure",
    ["device", "model", "missing_model", "missing_value", "missing_device", "endpoint"],
)
async def test_offline_and_recovery(source, failure):
    coordinator, _, derived = await site()
    original = coordinator.data
    changed = []
    for device in original.devices:
        if device.device_id != source:
            changed.append(device)
            continue
        if failure == "missing_device":
            continue
        if failure == "device":
            device = replace(device, available=False)
        elif failure == "model":
            device = replace(
                device,
                decoded_models=tuple(
                    replace(s, available=False) for s in device.decoded_models
                ),
            )
        elif failure == "missing_model":
            device = replace(device, decoded_models=())
        elif failure == "missing_value":
            device = replace(
                device,
                decoded_models=tuple(
                    replace(s, decoded=replace(s.decoded, fixed={}))
                    for s in device.decoded_models
                ),
            )
        changed.append(device)
    coordinator.data = FroniusPVCoordinatorData(devices=tuple(changed))
    if failure == "endpoint":
        coordinator.last_update_success = False
    assert all(not e.available and e.native_value is None for e in derived.values())
    coordinator.last_update_success = True
    coordinator.async_set_updated_data(original)
    assert all(e.available for e in derived.values())


@pytest.mark.asyncio
async def test_metadata_localization_and_discovery():
    coordinator, entities, derived = await site()
    original = coordinator.data
    ids = {key: e.unique_id for key, e in derived.items()}
    for language, names in [
        ("en", ["Consumption Power", "Autarky", "Self-Consumption"]),
        ("de", ["Verbrauchsleistung", "Autarkiegrad", "Eigenverbrauch"]),
    ]:
        data = json.loads(
            Path(
                f"custom_components/fronius_pv_manager/translations/{language}.json"
            ).read_text()
        )
        for key, name in zip(KEYS, names, strict=True):
            e = derived[key]
            assert e.unique_id == f"test-entry_device1_inverter_hlc_{key}"
            assert e.suggested_object_id == f"inverter_{key}"
            assert e.translation_key == key
            assert data["entity"]["sensor"][key]["name"] == name
            assert e.device_info["identifiers"] == {
                ("fronius_pv_manager", "test-entry:device1:inverter")
            }
            assert e.native_unit_of_measurement == (
                "W" if key == "consumption_power" else "%"
            )
            assert e.device_class == (
                SensorDeviceClass.POWER if key == "consumption_power" else None
            )
            assert e.state_class == SensorStateClass.MEASUREMENT
    coordinator.async_set_updated_data(
        replace(original, devices=(original.devices[0],))
    )
    assert all(not e.available for e in derived.values())
    coordinator.async_set_updated_data(original)
    assert all(e.available and e.unique_id == ids[key] for key, e in derived.items())
    assert len([e for e in entities if isinstance(e, SolarConsumptionSensor)]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["inverter", "meter"])
@pytest.mark.parametrize("online", [True, False])
async def test_ambiguous_topology_is_not_silently_selected(role, online):
    coordinator, _, derived = await site()
    original = coordinator.data
    extra = replace(
        original.devices[0 if role == "inverter" else 1],
        device_id=201,
        available=online,
    )
    coordinator.transports = {**coordinator.transports, 201: coordinator.transports[1]}
    coordinator.data = replace(original, devices=(*original.devices, extra))
    assert all(not e.available for e in derived.values())


@pytest.mark.asyncio
async def test_late_startup_topology_creates_entities_once():
    populated, _, _ = await site()
    coordinator, entry, transport = _coordinator_with()
    coordinator.transports = {1: transport, 200: transport}
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    for _ in range(2):
        coordinator.async_set_updated_data(populated.data)
    derived = [e for e in entities if isinstance(e, SolarConsumptionSensor)]
    assert len(derived) == 3
    assert all(e.available for e in derived)
