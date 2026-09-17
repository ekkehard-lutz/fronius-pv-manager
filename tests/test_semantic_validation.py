"""Malformed engineering values and ambiguous topology fail unavailable."""

from dataclasses import replace

import pytest

from custom_components.fronius_pv_manager.register_maps import (
    MODEL_103,
    MODEL_120,
    MODEL_160,
    MODEL_203,
)
from custom_components.fronius_pv_manager.sensor import SolarPowerSensor
from custom_components.fronius_pv_manager.topology import model_topology
from tests.test_consumption_sensors import site
from tests.test_efficiency_sensors import change, efficiency, fixed_snapshot, sources
from tests.test_power_sensors import mppts
from tests.test_rectifier_efficiency import entity, rectifier
from tests.test_sensor import _coordinator_with, _snapshot

INVALID = ["bad", True, float("nan"), float("inf"), -float("inf"), 10**309]


def power_sensor(snapshot, key):
    coordinator, _, _ = _coordinator_with(snapshot)
    return coordinator, SolarPowerSensor(
        coordinator, "test-entry", 1, key, "inverter" if key == "pv_power" else "meter"
    )


def unavailable(sensor):
    assert sensor.native_value is None
    assert not sensor.available


@pytest.mark.parametrize("invalid", [*INVALID, -1])
def test_invalid_pv_module_does_not_hide_behind_partial_sum(invalid):
    snapshots = change(
        (mppts([("MPPT 1", 100), ("MPPT 2", 200)]),), 160, "DCW", invalid, 0
    )
    _, sensor = power_sensor(snapshots[0], "pv_power")
    unavailable(sensor)


@pytest.mark.parametrize("key", ["grid_import_power", "grid_export_power"])
@pytest.mark.parametrize("invalid", INVALID)
def test_invalid_grid_power(key, invalid):
    snapshots = change((_snapshot(MODEL_203),), 203, "W", invalid)
    _, sensor = power_sensor(snapshots[0], key)
    unavailable(sensor)


@pytest.mark.parametrize(
    "key,model",
    [
        ("pv_power", MODEL_160),
        ("grid_import_power", MODEL_203),
        ("grid_export_power", MODEL_203),
    ],
)
@pytest.mark.parametrize(
    "failure", ["online", "offline", "first_offline", "discovered_only", "cached_only"]
)
def test_ambiguous_power_models(key, model, failure):
    snapshot = (
        mppts([("MPPT 1", 100)]) if model == MODEL_160 else fixed_snapshot(model, W=100)
    )
    coordinator, sensor = power_sensor(snapshot, key)
    device = coordinator.data.devices[0]
    duplicate = replace(
        snapshot, discovered=replace(snapshot.discovered, base_address=41000)
    )
    if failure == "cached_only":
        coordinator.topology = {
            "1": [
                model_topology(snapshot.discovered, snapshot.decoded),
                model_topology(duplicate.discovered),
            ]
        }
    else:
        decoded = (snapshot,)
        if failure != "discovered_only":
            decoded = (
                replace(snapshot, available=failure != "first_offline"),
                replace(duplicate, available=failure != "offline"),
            )
        coordinator.data = replace(
            coordinator.data,
            devices=(
                replace(
                    device,
                    discovered_models=(snapshot.discovered, duplicate.discovered),
                    decoded_models=decoded,
                ),
            ),
        )
    unavailable(sensor)


@pytest.mark.parametrize("missing", [None, "missing"])
def test_partial_pv_sum_remains_supported(missing):
    snapshots = change(
        (mppts([("MPPT 1", 100), ("MPPT 2", 200)]),), 160, "DCW", missing, 0
    )
    _, sensor = power_sensor(snapshots[0], "pv_power")
    assert sensor.native_value == 200 and sensor.available


@pytest.mark.parametrize("power", [1e308, 10**308])
def test_pv_aggregate_overflow(power):
    snapshots = (mppts([("MPPT 1", 100), ("MPPT 2", 100)]),)
    for index in (0, 1):
        snapshots = change(snapshots, 160, "DCW", power, index)
    _, sensor = power_sensor(snapshots[0], "pv_power")
    unavailable(sensor)


@pytest.mark.parametrize(
    "model,key,field,sf",
    [
        (MODEL_203, "grid_import_power", "W", "W_SF"),
        (MODEL_203, "grid_export_power", "W", "W_SF"),
    ],
)
def test_grid_raw_scaling_overflow(model, key, field, sf):
    _, sensor = power_sensor(fixed_snapshot(model, **{field: 1, sf: 309}), key)
    unavailable(sensor)


def test_pv_raw_scaling_overflow():
    # Use real decoder input, including the module name and DCW_SF=309.
    payload = [0] * 28
    payload[2], payload[6] = 309, 1
    from tests.test_sensor import _string_words

    payload[9:17] = _string_words("MPPT 1", 8)
    payload[19] = 1
    snapshot = _snapshot(MODEL_160, payload)
    _, sensor = power_sensor(snapshot, "pv_power")
    unavailable(sensor)


@pytest.mark.asyncio
@pytest.mark.parametrize("source,definition", [(1, MODEL_103), (200, MODEL_203)])
async def test_site_raw_scaling_overflow(source, definition):
    coordinator, _, sensors = await site()
    devices = []
    for device in coordinator.data.devices:
        if device.device_id == source:
            device = replace(
                device,
                decoded_models=tuple(
                    fixed_snapshot(definition, W=1, W_SF=309)
                    if s.discovered.model_id == definition.model_ids[0]
                    else s
                    for s in device.decoded_models
                ),
            )
        devices.append(device)
    coordinator.data = replace(coordinator.data, devices=tuple(devices))
    for sensor in sensors.values():
        unavailable(sensor)


@pytest.mark.parametrize("ac", [1, -1])
def test_conversion_raw_scaling_overflow(ac):
    snapshots = list(rectifier() if ac < 0 else sources())
    snapshots[0] = fixed_snapshot(MODEL_103, W=ac, W_SF=309, St=4)
    _, existing = efficiency(snapshots)
    unavailable(existing["inverter_efficiency"])
    _, sensor, _ = entity(snapshots)
    unavailable(sensor)


@pytest.mark.parametrize(
    "model,field,index",
    [
        (160, "DCW", 0),
        (160, "DCW", 1),
        (160, "DCW", 2),
    ],
)
def test_conversion_huge_dc_sources(model, field, index):
    _, sensors = efficiency(change(sources(), model, field, 10**309, index))
    unavailable(sensors["inverter_efficiency"])
    _, sensor, _ = entity(change(rectifier(), model, field, 10**309, index))
    unavailable(sensor)


@pytest.mark.parametrize(
    "model,field,index",
    [
        (120, "WHRtg", None),
        (124, "ChaState", None),
        (160, "DCWH", 1),
        (160, "DCWH", 2),
    ],
)
def test_lifetime_huge_sources(model, field, index):
    _, sensors = efficiency(change(sources(), model, field, 10**309, index))
    unavailable(sensors["battery_lifetime_efficiency"])


def test_lifetime_raw_scaling_overflow():
    snapshots = list(sources())
    snapshots[1] = fixed_snapshot(MODEL_120, WHRtg=1, WHRtg_SF=309)
    _, sensors = efficiency(snapshots)
    unavailable(sensors["battery_lifetime_efficiency"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ac,grid", [(1e308, 1e308), (10**308, 10**308), (-1e308, -1e308)]
)
async def test_consumption_arithmetic_overflow(ac, grid):
    _, _, sensors = await site(ac, grid)
    for sensor in sensors.values():
        unavailable(sensor)


@pytest.mark.asyncio
async def test_self_consumption_ratio_overflow_is_not_clamped():
    _, _, sensors = await site(1e-308, 1e308)
    unavailable(sensors["self_consumption"])
    assert sensors["consumption_power"].available


@pytest.mark.parametrize("kind", ["inverter", "rectifier", "lifetime"])
def test_efficiency_arithmetic_overflow(kind):
    if kind == "rectifier":
        _, sensor, _ = entity(rectifier(pv=0, charge=1e308, ac=-1e-308))
    else:
        snapshots = sources()
        if kind == "inverter":
            snapshots = change(snapshots, 103, "W", 10**308)
            snapshots = change(snapshots, 160, "DCW", 1e-308, 0)
        else:
            snapshots = change(snapshots, 160, "DCWH", 1e308, 2)
            snapshots = change(snapshots, 120, "WHRtg", 1e308)
            snapshots = change(snapshots, 124, "ChaState", 100)
        _, sensors = efficiency(snapshots)
        sensor = sensors[
            "inverter_efficiency"
            if kind == "inverter"
            else "battery_lifetime_efficiency"
        ]
    unavailable(sensor)
