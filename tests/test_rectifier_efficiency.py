"""Rectifier efficiency uses validated, same-device AC-to-DC power balances."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.fronius_pv_manager.sensor import (
    SolarEfficiencySensor,
    async_setup_entry,
)
from tests.test_efficiency_sensors import change, sources
from tests.test_sensor import _coordinator_with


def rectifier(pv=507, charge=1000.3, discharge=0, ac=-538.3):
    snapshots = sources()
    for model, field, value, index in [
        (103, "W", ac, None),
        (160, "DCW", pv, 0),
        (160, "DCW", charge, 1),
        (160, "DCW", discharge, 2),
    ]:
        snapshots = change(snapshots, model, field, value, index)
    return snapshots


def entity(snapshots=None):
    coordinator, entry, transport = _coordinator_with(
        *(rectifier() if snapshots is None else snapshots)
    )
    sensor = SolarEfficiencySensor(
        coordinator, entry.entry_id, 1, "rectifier_efficiency", "inverter"
    )
    return coordinator, sensor, transport


@pytest.mark.parametrize(
    ("pv", "charge", "discharge", "ac", "expected"),
    [
        (291.18, 300.08, 0, -48.48, 100 * 8.90 / 48.48),
        (281.34, 299.97, 0, -63.53, 100 * 18.63 / 63.53),
        (297.70, 350.06, 0, -95.94, 100 * 52.36 / 95.94),
        (281.46, 350.20, 0, -112.50, 100 * 68.74 / 112.50),
        (242.92, 500.71, 0, -304.16, 100 * 257.79 / 304.16),
        (528.40, 1000.50, 0, -520.30, 100 * 472.10 / 520.30),
        (507, 1000.30, 0, -538.30, 100 * 493.30 / 538.30),
        (0, 950.40, 0, -1001.10, 100 * 950.40 / 1001.10),
        (100, 400, 100, -250, 80),
        (0, 0.000001, 0, -0.000002, 50),
        (0, 110, 0, -100, 110),
        (306.44, 295.45, 0, -5.344, None),
        (100, 200, 0, 100, None),
        (100, 100, 0, -100, None),
        (100, 200, 0, 0, None),
        (200, 100, 0, 100, None),
    ],
)
def test_formula(pv, charge, discharge, ac, expected):
    coordinator, sensor, transport = entity(rectifier(pv, charge, discharge, ac))
    assert sensor.native_value == (
        pytest.approx(expected) if expected is not None else None
    )
    assert sensor.available == (expected is not None)
    if expected is not None:
        inverter = SolarEfficiencySensor(
            coordinator, "test-entry", 1, "inverter_efficiency", "inverter"
        )
        assert inverter.native_value is None and not inverter.available
    assert transport.read_calls == []


@pytest.mark.parametrize("state", [0, 1, 2, 3, 5, 6, 7, 8, None, "missing", 4])
def test_requires_decoded_mppt(state):
    _, sensor, _ = entity(change(rectifier(), 103, "St", state))
    assert sensor.native_value is None and not sensor.available


@pytest.mark.parametrize(
    "model,field,index",
    [(103, "W", None), (160, "DCW", 0), (160, "DCW", 1), (160, "DCW", 2)],
)
@pytest.mark.parametrize(
    "invalid", [None, "missing", "bad", True, float("nan"), float("inf"), -float("inf")]
)
def test_invalid_sources(model, field, index, invalid):
    _, sensor, _ = entity(change(rectifier(), model, field, invalid, index))
    assert sensor.native_value is None and not sensor.available


@pytest.mark.parametrize("index", [0, 1, 2])
def test_negative_dc_source(index):
    _, sensor, _ = entity(change(rectifier(), 160, "DCW", -1, index))
    assert sensor.native_value is None and not sensor.available


@pytest.mark.parametrize("model", [103, 160])
@pytest.mark.parametrize("failure", ["missing", "offline", "duplicate", "discovery"])
def test_required_models(model, failure):
    coordinator, sensor, _ = entity()
    device = coordinator.data.devices[0]
    snapshots = list(device.decoded_models)
    index = next(i for i, s in enumerate(snapshots) if s.discovered.model_id == model)
    discovered = device.discovered_models
    if failure == "missing":
        snapshots.pop(index)
    elif failure == "offline":
        snapshots[index] = replace(snapshots[index], available=False)
    elif failure == "duplicate":
        snapshots.append(snapshots[index])
    else:
        discovered = (*discovered, snapshots[index].discovered)
    coordinator.data = replace(
        coordinator.data,
        devices=(
            replace(
                device, decoded_models=tuple(snapshots), discovered_models=discovered
            ),
        ),
    )
    assert sensor.native_value is None and not sensor.available


@pytest.mark.parametrize(
    "index,failure",
    [
        (i, f)
        for i in (0, 1, 2)
        for f in ("missing", "unclassified", "duplicate")
        if i != 0 or f != "duplicate"
    ],
)
def test_required_modules(index, failure):
    snapshots = list(rectifier())
    dc = snapshots[-1]
    modules = list(dc.decoded.repeating["module"])
    if failure == "missing":
        modules.pop(index)
    elif failure == "duplicate":
        modules.append(replace(modules[index], instance_index=4))
    else:
        values = dict(modules[index].values)
        values["IDStr"] = replace(values["IDStr"], value="Unknown")
        modules[index] = replace(modules[index], values=values)
    snapshots[-1] = replace(
        dc, decoded=replace(dc.decoded, repeating={"module": tuple(modules)})
    )
    _, sensor, _ = entity(snapshots)
    assert sensor.native_value is None and not sensor.available


def test_all_mppts_are_summed():
    snapshots = list(rectifier(pv=100, charge=500, ac=-400))
    dc = snapshots[-1]
    modules = dc.decoded.repeating["module"]
    snapshots[-1] = replace(
        dc,
        decoded=replace(
            dc.decoded,
            repeating={"module": (*modules, replace(modules[0], instance_index=4))},
        ),
    )
    _, sensor, _ = entity(snapshots)
    assert sensor.native_value == pytest.approx(75)


@pytest.mark.parametrize(
    "failure", ["endpoint", "device", "missing_device", "other_device"]
)
def test_unavailable_and_no_cross_device_borrowing(failure):
    coordinator, sensor, _ = entity()
    device = coordinator.data.devices[0]
    if failure == "endpoint":
        coordinator.last_update_success = False
    elif failure == "device":
        coordinator.data = replace(
            coordinator.data, devices=(replace(device, available=False),)
        )
    elif failure == "missing_device":
        coordinator.data = replace(coordinator.data, devices=())
    else:
        coordinator.data = replace(
            coordinator.data,
            devices=(
                replace(
                    device,
                    decoded_models=tuple(
                        s for s in device.decoded_models if s.discovered.model_id != 160
                    ),
                ),
                replace(device, device_id=2),
            ),
        )
    assert sensor.native_value is None and not sensor.available


@pytest.mark.asyncio
async def test_discovery_metadata_and_recovery():
    coordinator, entry, transport = _coordinator_with()
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    populated, _, _ = entity()
    for _ in range(2):
        coordinator.async_set_updated_data(populated.data)
    found = [
        e
        for e in entities
        if isinstance(e, SolarEfficiencySensor) and e.key == "rectifier_efficiency"
    ]
    assert len(found) == 1
    sensor = found[0]
    assert sensor.available
    assert sensor.unique_id == "test-entry_device1_inverter_hlc_rectifier_efficiency"
    assert sensor.suggested_object_id == "inverter_rectifier_efficiency"
    assert sensor.translation_key == "rectifier_efficiency"
    assert sensor.native_unit_of_measurement == "%"
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.device_class is None
    assert sensor.device_info["identifiers"] == {
        ("fronius_pv_manager", "test-entry:device1:inverter")
    }
    for file, name in [
        ("strings.json", "Rectifier Efficiency"),
        ("translations/en.json", "Rectifier Efficiency"),
        ("translations/de.json", "Gleichrichter-Wirkungsgrad"),
    ]:
        data = json.loads(
            (Path("custom_components/fronius_pv_manager") / file).read_text()
        )
        assert data["entity"]["sensor"][sensor.key]["name"] == name
    coordinator.last_update_success = False
    assert not sensor.available and sensor.native_value is None
    coordinator.last_update_success = True
    coordinator.async_set_updated_data(populated.data)
    assert sensor.available
    assert transport.read_calls == []


@pytest.mark.parametrize("duplicate", [True, False])
def test_device_topology(duplicate):
    coordinator, sensor, _ = entity()
    device = coordinator.data.devices[0]
    coordinator.data = replace(
        coordinator.data,
        devices=(device, replace(device, device_id=1 if duplicate else 2)),
    )
    assert sensor.available == (not duplicate)
    if duplicate:
        assert sensor.native_value is None


@pytest.mark.parametrize(
    "pv,charge,discharge,ac",
    [
        (1e308, 1e308, 1e308, -100),
        (0, 1e308, 0, -1e-308),
    ],
)
def test_nonfinite_calculation(pv, charge, discharge, ac):
    _, sensor, _ = entity(rectifier(pv, charge, discharge, ac))
    assert sensor.native_value is None and not sensor.available
