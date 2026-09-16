"""Efficiency calculations use scaled SunSpec data, not enabled HA entities."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.fronius_pv_manager.register_maps import (
    MODEL_103,
    MODEL_120,
    MODEL_124,
    MODEL_160,
)
from custom_components.fronius_pv_manager.sensor import (
    SolarEfficiencySensor,
    async_setup_entry,
)
from tests.test_power_sensors import mppts
from tests.test_sensor import _coordinator_with, _snapshot


def fixed_snapshot(definition, **raw):
    words = [0] * definition.expected_length
    for register in definition.registers:
        if register.name in raw:
            value = raw[register.name]
            for i in range(register.size):
                words[register.offset + i] = (
                    value >> (16 * (register.size - 1 - i))
                ) & 0xFFFF
    return _snapshot(definition, words)


def sources(pv=5000, charge=0, discharge=0, ac=4800, state=4):
    dc = mppts(
        [("MPPT 1", pv), ("StCha", charge), ("StDisCha", discharge), ("Unknown", 60000)]
    )
    modules = list(dc.decoded.repeating["module"])
    for index, energy in [(1, 6453440), (2, 6034240)]:
        values = dict(modules[index].values)
        values["DCWH"] = replace(values["DCWH"], value=energy)
        modules[index] = replace(modules[index], values=values)
    dc = replace(dc, decoded=replace(dc.decoded, repeating={"module": tuple(modules)}))
    return (
        fixed_snapshot(MODEL_103, W=ac, St=state),
        fixed_snapshot(MODEL_120, WHRtg=1100, WHRtg_SF=1),
        fixed_snapshot(MODEL_124, ChaState=6800, ChaState_SF=-2),
        dc,
    )


def efficiency(snapshots=None, device_id=1):
    coordinator, entry, _ = _coordinator_with(
        *(sources() if snapshots is None else snapshots)
    )
    return coordinator, {
        key: SolarEfficiencySensor(coordinator, entry.entry_id, device_id, key, role)
        for key, role in [
            ("inverter_efficiency", "inverter"),
            ("battery_lifetime_efficiency", "storage"),
        ]
    }


def change(snapshots, model_id, field, value, module_index=None):
    result = []
    for snapshot in snapshots:
        if snapshot.discovered.model_id != model_id:
            result.append(snapshot)
            continue
        decoded = snapshot.decoded
        mapping = (
            decoded.fixed
            if module_index is None
            else decoded.repeating["module"][module_index].values
        )
        values = dict(mapping)
        if value == "missing":
            del values[field]
        else:
            values[field] = replace(values[field], value=value)
        if module_index is None:
            decoded = replace(decoded, fixed=values)
        else:
            modules = list(decoded.repeating["module"])
            modules[module_index] = replace(modules[module_index], values=values)
            decoded = replace(decoded, repeating={"module": tuple(modules)})
        result.append(replace(snapshot, decoded=decoded))
    return tuple(result)


@pytest.mark.parametrize(
    ("pv", "charge", "discharge", "ac", "expected"),
    [
        (5000, 0, 0, 4800, 96),
        (0, 0, 500, 475, 95),
        (2000, 0, 1000, 2880, 96),
        (5000, 2000, 0, 2880, 96),
        (5000, 2000, 1000, 3800, 95),
        (0, 0, 0, 0, None),
        (500, 500, 0, 0, None),
        (500, 1000, 0, 0, None),
        (500, 0, 0, -10, None),
        (500, 0, 0, 550, 110),
        (500, 0, 0, 0, 0),
    ],
)
def test_inverter_formula(pv, charge, discharge, ac, expected):
    _, entities = efficiency(sources(pv, charge, discharge, ac))
    entity = entities["inverter_efficiency"]
    assert entity.native_value == (
        pytest.approx(expected) if expected is not None else None
    )
    assert entity.available == (expected is not None)


@pytest.mark.parametrize("state", [0, 1, 2, 3, 5, 6, 7, 8, 0xFFFF])
def test_requires_mppt_enum(state):
    _, entities = efficiency(sources(state=state))
    assert not entities["inverter_efficiency"].available
    assert entities["battery_lifetime_efficiency"].available


@pytest.mark.parametrize(
    ("soc", "expected"),
    [
        (68, 100 * (6034240 + 7480) / 6453440),
        (0, 100 * 6034240 / 6453440),
        (100, 100 * (6034240 + 11000) / 6453440),
    ],
)
def test_lifetime_reference(soc, expected):
    _, entities = efficiency(change(sources(), 124, "ChaState", soc))
    assert entities["battery_lifetime_efficiency"].native_value == pytest.approx(
        expected
    )


# Every required numeric source gets the same invalid/missing/finite checks.
@pytest.mark.parametrize(
    ("key", "model", "field", "index"),
    [
        ("inverter_efficiency", 103, "W", None),
        ("inverter_efficiency", 160, "DCW", 0),
        ("inverter_efficiency", 160, "DCW", 1),
        ("inverter_efficiency", 160, "DCW", 2),
        ("battery_lifetime_efficiency", 160, "DCWH", 1),
        ("battery_lifetime_efficiency", 160, "DCWH", 2),
        ("battery_lifetime_efficiency", 120, "WHRtg", None),
        ("battery_lifetime_efficiency", 124, "ChaState", None),
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [None, "missing", "invalid", True, float("nan"), float("inf"), -float("inf"), -1],
)
def test_invalid_sources(key, model, field, index, invalid):
    _, entities = efficiency(change(sources(), model, field, invalid, index))
    assert entities[key].native_value is None
    assert not entities[key].available


@pytest.mark.parametrize(
    ("model", "field", "index", "value"),
    [
        (160, "DCWH", 1, 0),
        (120, "WHRtg", None, 0),
        (124, "ChaState", None, 101),
    ],
)
def test_lifetime_boundaries(model, field, index, value):
    _, entities = efficiency(change(sources(), model, field, value, index))
    assert not entities["battery_lifetime_efficiency"].available


def test_lifetime_above_100_not_clamped():
    snapshots = change(sources(), 160, "DCWH", 1000, 1)
    snapshots = change(snapshots, 160, "DCWH", 900, 2)
    _, entities = efficiency(snapshots)
    assert entities["battery_lifetime_efficiency"].native_value == pytest.approx(838)


@pytest.mark.parametrize("model_id", [103, 120, 124, 160])
@pytest.mark.parametrize("failure", ["offline", "missing", "duplicate"])
def test_model_availability_and_ambiguity(model_id, failure):
    snapshots = list(sources())
    index = next(
        i for i, s in enumerate(snapshots) if s.discovered.model_id == model_id
    )
    if failure == "offline":
        snapshots[index] = replace(snapshots[index], available=False)
    elif failure == "missing":
        snapshots.pop(index)
    else:
        snapshots.append(snapshots[index])
    _, entities = efficiency(snapshots)
    affected = (
        ["inverter_efficiency"] if model_id == 103 else ["battery_lifetime_efficiency"]
    )
    if model_id == 160:
        affected = list(entities)
    assert all(not entities[key].available for key in affected)


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("failure", ["duplicate", "missing", "unclassified"])
def test_storage_module_ambiguity(index, failure):
    snapshots = list(sources())
    dc = snapshots[-1]
    modules = list(dc.decoded.repeating["module"])
    if failure == "duplicate":
        modules.append(replace(modules[index], instance_index=4))
    elif failure == "missing":
        modules.pop(index)
    else:
        values = dict(modules[index].values)
        values["IDStr"] = replace(values["IDStr"], value="Unknown")
        modules[index] = replace(modules[index], values=values)
    snapshots[-1] = replace(
        dc, decoded=replace(dc.decoded, repeating={"module": tuple(modules)})
    )
    _, entities = efficiency(snapshots)
    assert all(not e.available for e in entities.values())


def test_multiple_mppts_unknown_modules_and_missing_pv():
    snapshots = list(sources())
    dc = snapshots[-1]
    modules = list(dc.decoded.repeating["module"])
    modules.append(replace(modules[0], instance_index=4))
    snapshots[-1] = replace(
        dc, decoded=replace(dc.decoded, repeating={"module": tuple(modules)})
    )
    _, entities = efficiency(snapshots)
    assert entities["inverter_efficiency"].native_value == 48
    snapshots = change(snapshots, 160, "DCW", None, 4)
    _, entities = efficiency(snapshots)
    assert not entities["inverter_efficiency"].available
    assert entities["battery_lifetime_efficiency"].available


def test_engineering_units_from_real_decoder():
    # Model 160 energies use DCWH_SF=1; power uses DCW_SF=-1.
    words = [0] * 68
    words[2], words[3], words[6] = 0xFFFF, 1, 3
    from tests.test_sensor import _string_words

    for index, (name, power, energy) in enumerate(
        [
            ("MPPT 1", 50000, 0),
            ("StCha", 20000, 645344),
            ("StDisCha", 0, 603424),
        ]
    ):
        offset = 8 + index * 20
        words[offset] = index + 1
        words[offset + 1 : offset + 9] = _string_words(name, 8)
        words[offset + 11] = power
        words[offset + 12 : offset + 14] = [energy >> 16, energy & 0xFFFF]
    snapshots = (
        fixed_snapshot(MODEL_103, W=28800, W_SF=-1, St=4),
        fixed_snapshot(MODEL_120, WHRtg=1100, WHRtg_SF=1),
        fixed_snapshot(MODEL_124, ChaState=6800, ChaState_SF=-2),
        _snapshot(MODEL_160, words),
    )
    _, entities = efficiency(snapshots)
    assert entities["inverter_efficiency"].native_value == pytest.approx(96)
    assert entities["battery_lifetime_efficiency"].native_value == pytest.approx(
        93.6201511116
    )
    assert snapshots[1].decoded.fixed["WHRtg"].value == 11000
    assert not next(
        r for r in MODEL_120.registers if r.name == "WHRtg"
    ).entity.enabled_by_default


@pytest.mark.asyncio
async def test_devices_discovery_metadata_and_offline_recovery():
    coordinator, entry, transport = _coordinator_with()
    entities = []
    await async_setup_entry(coordinator.hass, entry, entities.extend)
    assert not entities
    populated, _ = efficiency()
    original = populated.data
    for _ in range(2):
        coordinator.async_set_updated_data(original)
    derived = [e for e in entities if isinstance(e, SolarEfficiencySensor)]
    assert len(derived) == 2
    for entity in derived:
        assert entity.available
        assert entity.unique_id == f"test-entry_device1_{entity.role}_hlc_{entity.key}"
        assert entity.suggested_object_id == f"{entity.role}_{entity.key}"
        assert entity.role == (
            "inverter" if entity.key == "inverter_efficiency" else "storage"
        )
        assert entity.device_info["identifiers"] == {
            ("fronius_pv_manager", f"test-entry:device1:{entity.role}")
        }
        assert entity.native_unit_of_measurement == "%"
        assert entity.device_class is None
        assert entity.state_class == SensorStateClass.MEASUREMENT
        for lang in ["en", "de"]:
            data = json.loads(
                Path(
                    f"custom_components/fronius_pv_manager/translations/{lang}.json"
                ).read_text()
            )
            assert data["entity"]["sensor"][entity.translation_key]["name"]
    coordinator.last_update_success = False
    assert all(not e.available for e in derived)
    coordinator.last_update_success = True
    coordinator.async_set_updated_data(
        replace(original, devices=(replace(original.devices[0], available=False),))
    )
    assert all(not e.available for e in derived)
    coordinator.async_set_updated_data(original)
    assert all(e.available for e in derived)
    assert transport.read_calls == []


def test_no_cross_device_source_borrowing():
    coordinator, entities = efficiency()
    original = coordinator.data.devices[0]
    # Capacity lives only on another Modbus ID: never borrow it.
    models = tuple(s for s in original.decoded_models if s.discovered.model_id != 120)
    coordinator.data = replace(
        coordinator.data,
        devices=(
            replace(original, decoded_models=models),
            replace(original, device_id=2),
        ),
    )
    assert not entities["battery_lifetime_efficiency"].available
    assert entities["inverter_efficiency"].available


@pytest.mark.parametrize(
    "state", ["missing", None, "mppt", "MPP-Betrieb", float("nan"), float("inf"), 4]
)
def test_missing_or_invalid_decoded_operating_state(state):
    _, entities = efficiency(change(sources(), 103, "St", state))
    assert not entities["inverter_efficiency"].available


def test_no_mppt_and_zero_discharge_counter():
    snapshots = change(sources(), 160, "IDStr", "Unknown", 0)
    snapshots = change(snapshots, 160, "DCWH", 0, 2)
    _, entities = efficiency(snapshots)
    assert not entities["inverter_efficiency"].available
    assert entities["battery_lifetime_efficiency"].native_value == pytest.approx(
        100 * 7480 / 6453440
    )
