"""Polling option validation, defaults and the normal unload/setup lifecycle."""

import inspect
from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

import custom_components.fronius_pv_manager as integration
from custom_components.fronius_pv_manager.config_flow import FroniusPVManagerConfigFlow
from custom_components.fronius_pv_manager.const import (
    CONF_FAST_SCAN_INTERVAL as FAST,
)
from custom_components.fronius_pv_manager.const import (
    CONF_SLOW_SCAN_INTERVAL as SLOW,
)
from tests.runtime_fakes import (
    FakeEndpoint,
    FakeEntry,
    FakeHass,
    FakeTransport,
    model_chain,
)


def options_flow(options=None):
    entry = FakeEntry({"host": "192.0.2.1"})
    entry.options = options or {}
    hass = FakeHass()
    hass.config_entries.async_get_known_entry = lambda entry_id: entry
    flow = FroniusPVManagerConfigFlow.async_get_options_flow(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow, entry


@pytest.mark.asyncio
async def test_existing_entry_options_default_to_5_30():
    flow, _ = options_flow()
    result = await flow.async_step_init()
    assert result["data_schema"]({}) == {
        FAST: 5,
        SLOW: 30,
        "diagnostic_level": 0,
        "communication_availability_timeout": 300,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fast,slow", [(2, 10), (5, 30), (7, 30), (30, 30), (30, 300)])
async def test_valid_options_persist_both_values_and_preserve_other_options(fast, slow):
    flow, entry = options_flow({"unrelated": True})
    result = await flow.async_step_init({FAST: fast, SLOW: slow})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        FAST: fast,
        SLOW: slow,
        "diagnostic_level": 0,
        "communication_availability_timeout": 300,
        "unrelated": True,
    }
    # OptionsFlowManager persists the returned data, then subsequent forms reuse it.
    entry.options = result["data"]
    result = await flow.async_step_init()
    assert result["data_schema"]({}) == {
        FAST: fast,
        SLOW: slow,
        "diagnostic_level": 0,
        "communication_availability_timeout": 300,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fast,slow,key,error",
    [
        (1, 30, FAST, "invalid_interval"),
        (31, 60, FAST, "invalid_interval"),
        (5, 9, SLOW, "invalid_interval"),
        (5, 301, SLOW, "invalid_interval"),
        (20, 10, SLOW, "slow_less_than_fast"),
        (2.5, 30, FAST, "invalid_interval"),
        (True, 30, FAST, "invalid_interval"),
    ],
)
async def test_invalid_options_are_clean_form_errors(fast, slow, key, error):
    flow, entry = options_flow()
    result = await flow.async_step_init({FAST: fast, SLOW: slow})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {key: error}
    assert entry.options == {}
    assert (
        next(k for k in result["data_schema"].schema if k.schema == FAST).default()
        == fast
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options", [{FAST: 7, SLOW: 30}, {FAST: 5, SLOW: 60}, {FAST: 7, SLOW: 40}]
)
async def test_option_listener_reloads_runtime_and_cancels_old_timer(
    monkeypatch, options
):
    flow, entry = options_flow()
    hass = flow.hass
    endpoints = []

    def factory(*args, **kwargs):
        registers, _ = model_chain((1, 65), (103, 50))
        endpoint = FakeEndpoint({1: FakeTransport(registers)})
        endpoints.append(endpoint)
        return endpoint

    monkeypatch.setattr(integration, "ModbusTcpEndpointTransport", factory)
    await integration.async_setup_entry(hass, entry)
    old = entry.runtime_data
    old.async_add_listener(lambda: None)
    assert old._unsub_refresh is not None
    listeners = tuple(entry.update_listeners)
    assert len(listeners) == 1

    async def reload(entry_id):
        assert entry_id == entry.entry_id
        assert await integration.async_unload_entry(hass, entry)
        # ConfigEntry invokes registered unload callbacks after unload succeeds.
        for callback in entry.unload_callbacks:
            result = callback()
            if inspect.isawaitable(result):
                await result
        entry.unload_callbacks.clear()
        assert await integration.async_setup_entry(hass, entry)

    hass.config_entries.async_reload = AsyncMock(side_effect=reload)
    # Discovery persists entry.data too; that must not cause a reload loop.
    await listeners[0](hass, entry)
    hass.config_entries.async_reload.assert_not_called()
    result = await flow.async_step_init(options)
    entry.options = result["data"]
    await listeners[0](hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
    new = entry.runtime_data
    assert new is not old
    assert old._shutdown_requested and old._closed
    assert old._unsub_refresh is None
    assert not old._update_tasks
    assert endpoints[0].close_calls == 1
    assert len(entry.update_listeners) == 1
    assert new.fast_interval == options[FAST]
    assert new.slow_interval == options[SLOW]
    assert new.update_interval.total_seconds() == options[FAST]
    assert new.data.discovered_models == old.data.discovered_models
    await integration.async_unload_entry(hass, entry)
