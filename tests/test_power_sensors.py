"""Semantic power flows reuse decoded sources and physical device identities."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinatorData
from custom_components.fronius_pv_manager.register_maps import (
    MODEL_103,
    MODEL_160,
    MODEL_203,
)
from custom_components.fronius_pv_manager.sensor import (
    FroniusPVSensor,
    SolarPowerSensor,
    async_setup_entry,
)
from tests.test_sensor import _coordinator_with, _snapshot, _string_words


def mppts(modules):
    payload = [0] * (8 + 20 * len(modules))
    payload[6] = len(modules)
    for index, (name, power) in enumerate(modules):
        offset = 8 + 20 * index
        payload[offset] = index + 1
        payload[offset + 1 : offset + 9] = _string_words(name, 8)
        payload[offset + 11] = 0xFFFF if power is None else power
    return _snapshot(MODEL_160, payload)


async def setup(*snapshots):
    coordinator, entry, transport = _coordinator_with(*snapshots)
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    return coordinator, entities, transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("modules", "expected"),
    [
        ([("MPPT 1", 123)], 123),
        ([("MPPT 1", 0)], 0),
        ([("MPPT west", 100), ("MPPT east", 200), ("MPPT roof", 300)], 600),
        ([("MPPT 1", 100), ("StCha", 800), ("StDisCha", 900), ("Unknown", 700)], 100),
        ([("MPPT 1", None), ("MPPT 2", 200)], 200),
        ([("MPPT 1", None)], None),
        ([("StCha", 800), ("StDisCha", 900)], None),
        ([], None),
    ],
)
async def test_pv(modules, expected):
    aggregate = [0] * 50
    aggregate[29] = 9000  # Model 103 DCW must never contribute.
    _, entities, transport = await setup(
        _snapshot(MODEL_103, aggregate), mppts(modules)
    )
    pv = next(e for e in entities if isinstance(e, SolarPowerSensor))
    assert pv.native_value == expected
    assert pv.available == (expected is not None)
    raw = [e for e in entities if isinstance(e, FroniusPVSensor)]
    assert (
        next(
            e
            for e in raw
            if e._source.register_name == "DCW" and e._source.model_id == 103
        ).native_value
        == 9000
    )
    assert transport.read_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signed", "expected"),
    [
        (1234, (1234, 0)),
        (-1234, (0, 1234)),
        (0, (0, 0)),
        (None, (None, None)),
    ],
)
async def test_grid(signed, expected):
    """Model 203 grid-location convention: positive import, negative export."""
    payload = [0] * 105
    payload[16] = 0x8000 if signed is None else signed & 0xFFFF
    _, entities, _ = await setup(_snapshot(MODEL_203, payload))
    power = {e.key: e for e in entities if isinstance(e, SolarPowerSensor)}
    values = tuple(
        power[key].native_value for key in ("grid_import_power", "grid_export_power")
    )
    assert values == expected
    assert all(e.available == (signed is not None) for e in power.values())
    if signed is not None:
        assert all(v >= 0 for v in values)
        assert sum(v > 0 for v in values) == (signed != 0)
    raw = next(
        e
        for e in entities
        if isinstance(e, FroniusPVSensor) and e._source.register_name == "W"
    )
    assert raw.native_value == signed
    assert raw.unique_id == "test-entry_device1_meter_model_203_w"
    assert raw.suggested_object_id == "ac_power"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["endpoint", "device", "model", "missing_model", "missing_device", "missing_value"],
)
async def test_unavailable_sources(failure):
    coordinator, entities, _ = await setup(
        mppts([("MPPT 1", 200)]), _snapshot(MODEL_203)
    )
    power = [e for e in entities if isinstance(e, SolarPowerSensor)]
    device = coordinator.data.devices[0]
    if failure == "endpoint":
        coordinator.last_update_success = False
    elif failure == "device":
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
        pv, meter = device.decoded_models
        module = pv.decoded.repeating["module"][0]
        module = replace(
            module, values={k: v for k, v in module.values.items() if k != "DCW"}
        )
        pv = replace(pv, decoded=replace(pv.decoded, repeating={"module": (module,)}))
        meter = replace(meter, decoded=replace(meter.decoded, fixed={}))
        device = replace(device, decoded_models=(pv, meter))
    coordinator.data = FroniusPVCoordinatorData(
        devices=() if failure == "missing_device" else (device,)
    )
    assert len(power) == 3
    assert all(not e.available and e.native_value is None for e in power)


@pytest.mark.asyncio
async def test_discovery_metadata_and_identity():
    coordinator, entry, _ = _coordinator_with()
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    current, _, _ = _coordinator_with(mppts([("MPPT 1", 100)]), _snapshot(MODEL_203))
    coordinator.async_set_updated_data(current.data)
    power = [e for e in entities if isinstance(e, SolarPowerSensor)]
    assert len(power) == 3
    coordinator.async_set_updated_data(current.data)
    assert len([e for e in entities if isinstance(e, SolarPowerSensor)]) == 3
    for lang in ("en", "de"):
        translations = json.loads(
            Path(
                f"custom_components/fronius_pv_manager/translations/{lang}.json"
            ).read_text()
        )
        for entity in power:
            role = "inverter" if entity.key == "pv_power" else "meter"
            assert entity.unique_id == f"test-entry_device1_{role}_hlc_{entity.key}"
            assert entity.suggested_object_id == f"{role}_{entity.key}"
            assert entity.device_info["identifiers"] == {
                ("fronius_pv_manager", f"test-entry:device1:{role}")
            }
            assert entity.native_unit_of_measurement == "W"
            assert entity.device_class == SensorDeviceClass.POWER
            assert entity.state_class == SensorStateClass.MEASUREMENT
            assert translations["entity"]["sensor"][entity.translation_key]["name"]
    # A newly discovered MPPT changes the existing entity without a reload.
    updated, _, _ = _coordinator_with(
        mppts([("MPPT 1", 100), ("MPPT 2", 200)]), _snapshot(MODEL_203)
    )
    coordinator.async_set_updated_data(updated.data)
    assert next(e for e in power if e.key == "pv_power").native_value == 300


@pytest.mark.asyncio
async def test_physical_devices_and_source_independence():
    """Each device retains its own readings, identity, and availability."""
    inverter, _, _ = _coordinator_with(mppts([("MPPT 1", 100)]))
    payload = [0] * 105
    payload[16] = (-1234) & 0xFFFF
    payload[20] = 0xFFFF  # Already-decoded W scale factor is -1.
    meter, _, _ = _coordinator_with(_snapshot(MODEL_203, payload))
    coordinator, entry, _ = _coordinator_with()
    coordinator = type(coordinator)(
        coordinator.hass,
        entry,
        {1: coordinator.transports[1], 200: coordinator.transports[1]},
    )
    coordinator.last_update_success = True
    entry.runtime_data = coordinator
    coordinator.data = FroniusPVCoordinatorData(
        devices=(
            inverter.data.devices[0],
            replace(meter.data.devices[0], device_id=200),
        )
    )
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    powers = [e for e in entities if isinstance(e, SolarPowerSensor)]
    assert len(powers) == 3
    for entity in powers:
        device_id = 1 if entity.key == "pv_power" else 200
        assert entity.device_id == device_id
        assert entity.device_info["identifiers"] == {
            ("fronius_pv_manager", f"test-entry:device{device_id}:{entity.role}")
        }
    assert next(
        e for e in powers if e.key == "grid_export_power"
    ).native_value == pytest.approx(123.4)
    coordinator.async_set_updated_data(
        replace(
            coordinator.data,
            devices=(
                replace(coordinator.data.devices[0], available=False),
                coordinator.data.devices[1],
            ),
        )
    )
    assert all(e.available == (e.device_id == 200) for e in powers)
