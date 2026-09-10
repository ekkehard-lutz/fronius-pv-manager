"""Temporary hardware harness routes validated calls through real remote APIs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.const import DOMAIN
from custom_components.fronius_pv_manager.debug_services import (
    SCHEMAS,
    TEST_OWNER,
    async_register_debug_services,
    async_unregister_debug_services,
    vol,
)
from tests.test_storage_remote import PROFILE
from tests.test_storage_remote import remote as remote_fixture

remote = remote_fixture


def register(coordinator, entry_id="test"):
    entry = SimpleNamespace(entry_id=entry_id, runtime_data=coordinator)
    async_register_debug_services(coordinator.hass, entry)
    return entry


async def call(coordinator, service, **data):
    handler, schema = coordinator.hass.services.handlers[DOMAIN, service]
    await handler(
        SimpleNamespace(service=service, data=schema({"device_id": 1, **data}))
    )


@pytest.mark.asyncio
async def test_harness_uses_real_apis_and_heartbeat_has_no_writes(remote):
    coordinator, control, clock = remote
    register(coordinator)
    methods = (
        "async_acquire_remote_control",
        "async_set_remote_power_window",
        "async_remote_heartbeat",
        "async_release_remote_control",
        "async_set_remote_minimum_reserve",
        "async_set_remote_grid_charging_allowed",
    )
    for name in methods:
        setattr(control, name, AsyncMock(wraps=getattr(control, name)))
    await call(coordinator, "debug_remote_acquire")
    control.async_acquire_remote_control.assert_awaited_once_with(1, TEST_OWNER, None)
    assert control.remote_owner(1) == TEST_OWNER
    await call(coordinator, "debug_remote_set_window", **PROFILE.__dict__)
    control.async_set_remote_power_window.assert_awaited_once_with(
        1, TEST_OWNER, PROFILE
    )
    assert control.values(1) == PROFILE
    writes = list(coordinator.control_transport.write_calls)
    clock.now += 20
    await call(coordinator, "debug_remote_heartbeat")
    assert coordinator.control_transport.write_calls == writes
    assert control._leases[1].expires_at == clock.now + 90
    await call(coordinator, "debug_remote_set_minimum_reserve", value=25)
    control.async_set_remote_minimum_reserve.assert_awaited_once_with(1, TEST_OWNER, 25)
    assert control.snapshot(1)["MinRsvPct"].value == 25
    await call(coordinator, "debug_remote_set_grid_charging_allowed", enabled=True)
    control.async_set_remote_grid_charging_allowed.assert_awaited_once_with(
        1, TEST_OWNER, True
    )
    assert control.snapshot(1)["ChaGriSet"].raw == 1
    await call(coordinator, "debug_remote_release")
    control.async_release_remote_control.assert_awaited_once_with(1, TEST_OWNER)
    assert control.mode(1) == "automatic"
    assert control.remote_owner(1) is None


@pytest.mark.asyncio
async def test_service_registration_routing_and_cleanup(remote):
    coordinator, control, _ = remote
    first = register(coordinator, "first")
    register(coordinator, "first")
    second = register(coordinator, "second")
    assert coordinator.hass.services.registrations == 6
    with pytest.raises(ServiceValidationError, match="ambiguous"):
        await call(coordinator, "debug_remote_acquire")
    with pytest.raises(ServiceValidationError, match="unavailable"):
        await call(coordinator, "debug_remote_acquire", config_entry_id="missing")
    assert not coordinator.control_transport.write_calls
    await call(coordinator, "debug_remote_acquire", config_entry_id="second")
    assert control.remote_owner(1) == TEST_OWNER
    async_unregister_debug_services(coordinator.hass, first)
    assert len(coordinator.hass.services.handlers) == 6
    async_unregister_debug_services(coordinator.hass, second)
    assert not coordinator.hass.services.handlers
    register(coordinator)
    assert len(coordinator.hass.services.handlers) == 6


@pytest.mark.parametrize(
    "service,data",
    [
        ("debug_remote_acquire", {"minimum_charge_power": 1}),
        ("debug_remote_acquire", {"owner_id": "injected"}),
        ("debug_remote_set_window", {}),
        ("debug_remote_set_window", {**PROFILE.__dict__, "minimum_charge_power": 1.5}),
        ("debug_remote_set_window", {**PROFILE.__dict__, "minimum_charge_power": True}),
        ("debug_remote_set_minimum_reserve", {"value": 4}),
        ("debug_remote_set_minimum_reserve", {"value": 101}),
        ("debug_remote_set_minimum_reserve", {"value": float("nan")}),
        ("debug_remote_set_grid_charging_allowed", {"enabled": 1}),
        ("debug_remote_heartbeat", {"device_id": "1"}),
        ("debug_remote_release", {"device_id": 0}),
    ],
)
def test_invalid_service_schemas(service, data):
    with pytest.raises(vol.Invalid):
        SCHEMAS[service]({"device_id": 1, **data})


@pytest.mark.asyncio
async def test_real_ha_service_dispatch(remote, tmp_path):
    from homeassistant.core import HomeAssistant

    coordinator, control, _ = remote
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(entry_id="real-ha", runtime_data=coordinator)
    try:
        async_register_debug_services(hass, entry)
        await hass.services.async_call(
            DOMAIN, "debug_remote_acquire", {"device_id": 1}, blocking=True
        )
        assert control.remote_owner(1) == TEST_OWNER
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN,
                "debug_remote_acquire",
                {"device_id": 1, "minimum_charge_power": 1},
                blocking=True,
            )
        async_unregister_debug_services(hass, entry)
        assert not hass.services.has_service(DOMAIN, "debug_remote_acquire")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_distinct_endpoint_routing(remote):
    coordinator, control, _ = remote
    register(coordinator, "first")
    other_control = SimpleNamespace(async_acquire_remote_control=AsyncMock())
    other = SimpleNamespace(transports={1: object()}, storage_control=other_control)
    entry = SimpleNamespace(entry_id="second", runtime_data=other)
    async_register_debug_services(coordinator.hass, entry)
    await call(coordinator, "debug_remote_acquire", config_entry_id="second")
    other_control.async_acquire_remote_control.assert_awaited_once_with(
        1, TEST_OWNER, None
    )
    assert control.remote_owner(1) is None
    assert not coordinator.control_transport.write_calls
