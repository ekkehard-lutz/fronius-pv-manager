"""Optional HTTP semantics, entity identity and independent runtime recovery."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.fronius_pv_manager import async_setup_entry
from custom_components.fronius_pv_manager import solar_api as api
from custom_components.fronius_pv_manager.binary_sensor import SolarBinarySensor
from custom_components.fronius_pv_manager.sensor import (
    BatteryOperationMode,
    FroniusPVSensor,
)
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain
from tests.test_init import install_endpoint_factory


def payload(mode="normal", backup=False, standby=True):
    return {
        "Body": {
            "Data": {
                "Site": {"BackupMode": backup, "BatteryStandby": standby},
                "Inverters": {
                    "1": {"Battery_Mode": "wrong"},
                    "42": {"Battery_Mode": mode},
                },
            }
        }
    }


@pytest.mark.parametrize("mode", ["normal", "charge boost", " Future MODE / X "])
def test_parser_preserves_strings(mode):
    state = api.parse_solar_state(payload(mode))
    assert state.battery_modes["42"] == mode
    assert state.backup_mode is False
    assert state.battery_standby is True


@pytest.mark.parametrize("bad", [None, [], 1, True, {}, "", "   "])
def test_invalid_mode_preserves_siblings(bad):
    state = api.parse_solar_state(payload(bad))
    assert "42" not in state.battery_modes
    assert state.backup_mode is False
    assert state.battery_standby is True


@pytest.mark.parametrize("bad", [None, [], {}, 0, 1, "false"])
@pytest.mark.parametrize("field", ["BackupMode", "BatteryStandby"])
def test_strict_booleans(bad, field):
    data = payload()
    data["Body"]["Data"]["Site"][field] = bad
    state = api.parse_solar_state(data)
    assert (
        getattr(state, "backup_mode" if field == "BackupMode" else "battery_standby")
        is None
    )
    assert state.battery_modes["42"] == "normal"


@pytest.mark.parametrize("data", [None, [], {}, {"Body": []}, {"Body": {"Data": None}}])
def test_missing_structures(data):
    assert api.parse_solar_state(data) == api.SolarState()


@pytest.mark.parametrize("field", ["Site", "Inverters"])
def test_missing_sections(field):
    data = payload()
    del data["Body"]["Data"][field]
    state = api.parse_solar_state(data)
    if field == "Site":
        assert state.backup_mode is None
        assert state.battery_modes["42"] == "normal"
    else:
        assert not state.battery_modes
        assert state.battery_standby is True


@pytest.mark.parametrize("field", ["BackupMode", "BatteryStandby", "Battery_Mode"])
def test_missing_field(field):
    data = payload()
    target = data["Body"]["Data"]
    del (target["Inverters"]["42"] if field == "Battery_Mode" else target["Site"])[
        field
    ]
    state = api.parse_solar_state(data)
    assert (
        state.battery_modes.get("42")
        if field == "Battery_Mode"
        else getattr(
            state, "backup_mode" if field == "BackupMode" else "battery_standby"
        )
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "connection", "timeout", "http", "json", "redirect"]
)
async def test_http(monkeypatch, solar_offline, failure):
    response = MagicMock(
        status=503 if failure == "http" else 302 if failure == "redirect" else 200
    )
    response.json = AsyncMock(return_value=payload())
    if failure == "json":
        response.json.side_effect = ValueError("bad JSON")
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    if failure in {"connection", "timeout"}:
        context.__aenter__.side_effect = (
            aiohttp.ClientConnectionError()
            if failure == "connection"
            else TimeoutError()
        )
    session = MagicMock()
    session.get.return_value = context
    monkeypatch.setattr(api, "async_get_clientsession", lambda hass: session)
    client = api.SolarAPI(FakeHass(), "192.0.2.42")
    state = await solar_offline(client)
    assert state == (
        api.parse_solar_state(payload()) if failure is None else api.SolarState()
    )
    args, kwargs = session.get.call_args
    assert (
        str(args[0]) == "http://192.0.2.42/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
    )
    assert kwargs["timeout"].total == 3
    assert kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_runtime_recovery_without_reload(monkeypatch):
    from custom_components.fronius_pv_manager import binary_sensor, sensor

    registers, _ = model_chain((103, 50), (124, 24))
    transport = FakeTransport(registers)
    install_endpoint_factory(monkeypatch, {42: transport})
    entry = FakeEntry({"host": "192.0.2.42", "device_id": 42})
    hass = FakeHass()
    assert await async_setup_entry(hass, entry)
    coordinator = entry.runtime_data
    entities = []
    await sensor.async_setup_entry(hass, entry, entities.extend)
    await binary_sensor.async_setup_entry(hass, entry, entities.extend)
    solar = [
        e for e in entities if isinstance(e, (BatteryOperationMode, SolarBinarySensor))
    ]
    modbus = [e for e in entities if isinstance(e, FroniusPVSensor)]
    assert len(solar) == 3
    assert modbus and all(e.available for e in modbus)
    assert all(not e.available for e in solar)
    for data in [payload(), payload(" Future MODE / X ", True, False), None, payload()]:
        coordinator.solar_api.async_poll = AsyncMock(
            return_value=api.parse_solar_state(data)
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert all(e.available for e in modbus)
        assert all(e.available == (data is not None) for e in solar)
        if data:
            values = {e.key: e.value for e in solar}
            assert (
                values["battery_operation_mode"]
                == data["Body"]["Data"]["Inverters"]["42"]["Battery_Mode"]
            )
            assert values["backup_mode"] is data["Body"]["Data"]["Site"]["BackupMode"]
            assert (
                values["battery_standby"]
                is data["Body"]["Data"]["Site"]["BatteryStandby"]
            )
    assert len(hass.config_entries.forwarded) == 1
    for entity in solar:
        assert f"device42_{entity.role}_hlc_" in entity.unique_id
        assert entity.device_info["identifiers"] == {
            ("fronius_pv_manager", f"{entry.entry_id}:device42:{entity.role}")
        }
    mode = next(e for e in solar if isinstance(e, BatteryOperationMode))
    assert mode.device_class is None
    assert mode.options is None
    assert mode.native_value == "normal"
    for entity in solar:
        if isinstance(entity, SolarBinarySensor):
            assert entity.is_on is entity.value


def test_translation_keys_preserve_raw_states():
    root = Path("custom_components/fronius_pv_manager/translations")
    for lang in ["en", "de"]:
        translations = json.loads((root / f"{lang}.json").read_text())
        states = translations["entity"]["sensor"]["battery_operation_mode"]["state"]
        assert states["normal"] == ("Normal" if lang == "en" else "Normalbetrieb")
        raw = " Future MODE / X "
        assert states.get(raw, raw) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["BackupMode", "BatteryStandby", "Battery_Mode"])
async def test_entity_field_independence(field):
    from types import SimpleNamespace

    data = payload()
    target = data["Body"]["Data"]
    del (target["Inverters"]["42"] if field == "Battery_Mode" else target["Site"])[
        field
    ]
    coordinator = SimpleNamespace(solar_state=api.parse_solar_state(data))
    entities = [
        BatteryOperationMode(
            coordinator, "entry", 42, "battery_operation_mode", "storage"
        ),
        SolarBinarySensor(coordinator, "entry", 42, "backup_mode", "inverter"),
        SolarBinarySensor(coordinator, "entry", 42, "battery_standby", "storage"),
    ]
    invalid = {
        "Battery_Mode": "battery_operation_mode",
        "BackupMode": "backup_mode",
        "BatteryStandby": "battery_standby",
    }[field]
    assert all(e.available == (e.key != invalid) for e in entities)


@pytest.mark.asyncio
async def test_cancelled_http_propagates(monkeypatch, solar_offline):
    import asyncio

    session = MagicMock()
    session.get.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(api, "async_get_clientsession", lambda hass: session)
    with pytest.raises(asyncio.CancelledError):
        await solar_offline(api.SolarAPI(FakeHass(), "inverter.local"))
