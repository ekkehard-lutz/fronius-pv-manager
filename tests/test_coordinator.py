"""Tests for the Home Assistant SunSpec runtime coordinator."""

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from tests.runtime_fakes import (
    FakeEndpoint,
    FakeEntry,
    FakeHass,
    FakeTransport,
    model_chain,
)


def coordinator_with_models(*models: tuple[int, int]):
    """Create a coordinator and synthetic transport for selected model IDs."""
    registers, bases = model_chain(*models)
    hass = FakeHass()
    entry = FakeEntry({})
    transport = FakeTransport(registers)
    coordinator = FroniusPVCoordinator(hass, entry, {1: transport})
    return coordinator, hass, transport, bases


@pytest.mark.asyncio
async def test_initial_discovery_preserves_supported_and_unknown_topology() -> None:
    """Discovery runs once and retains unknown models without decoding them."""
    coordinator, hass, transport, _ = coordinator_with_models((1, 65), (999, 2))

    await coordinator.async_discover()
    data = await coordinator._async_update_data()

    assert [model.model_id for model in coordinator.discovered_models] == [1, 999]
    assert data.discovered_models == coordinator.discovered_models
    assert [snapshot.discovered.model_id for snapshot in data.decoded_models] == [1]
    assert data.decoded_models[0].definition.model_ids == (1,)
    assert data.decoded_models[0].decoded.fixed["DA"].value == 0
    assert transport.connect_calls == 1
    assert [job.__name__ for job in hass.executor_jobs] == [
        "_connect_and_discover",
        "_poll_devices",
    ]


@pytest.mark.asyncio
async def test_refresh_reads_cached_payload_without_repeating_discovery() -> None:
    """Normal polling reads cached model payloads and never walks headers again."""
    coordinator, _, transport, bases = coordinator_with_models((1, 65))
    await coordinator.async_discover()
    discovery_reads = tuple(transport.read_calls)

    data = await coordinator._async_update_data()

    assert (bases[1], 65) in transport.read_calls
    assert tuple(transport.read_calls[: len(discovery_reads)]) == discovery_reads
    assert transport.connect_calls == 1
    assert len(data.decoded_models) == 1


@pytest.mark.asyncio
async def test_only_device_failure_marks_coordinator_unavailable_and_recovers() -> None:
    """A sole failed device fails the refresh and later recovers cleanly."""
    coordinator, _, transport, _ = coordinator_with_models((1, 65))
    await coordinator.async_discover()
    transport.fail_reads = True

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    transport.fail_reads = False
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.devices[0].available
    assert coordinator.data.decoded_models[0].discovered.model_id == 1


@pytest.mark.asyncio
async def test_close_is_delegated_to_executor() -> None:
    """Persistent transport closure never runs directly in async coordinator code."""
    coordinator, hass, transport, _ = coordinator_with_models((1, 65))
    await coordinator.async_close()
    assert transport.close_calls == 1
    assert hass.executor_jobs[-1].__name__ == "_close_transports"


@pytest.mark.asyncio
async def test_multiple_device_ids_are_discovered_and_polled_independently() -> None:
    """Each device retains its topology even when model IDs overlap."""
    registers_1, bases_1 = model_chain((103, 50))
    registers_200, bases_200 = model_chain((203, 105), (999, 2))
    transports = {
        1: FakeTransport(registers_1),
        200: FakeTransport(registers_200),
    }
    hass = FakeHass()
    coordinator = FroniusPVCoordinator(hass, FakeEntry({}), transports)

    await coordinator.async_discover()
    data = await coordinator._async_update_data()

    assert [device.device_id for device in data.devices] == [1, 200]
    assert [
        [model.model_id for model in device.discovered_models]
        for device in data.devices
    ] == [[103], [203, 999]]
    assert (bases_1[103], 50) in transports[1].read_calls
    assert (bases_200[203], 100) in transports[200].read_calls
    assert (bases_200[203] + 100, 5) in transports[200].read_calls
    assert all(transport.connect_calls == 1 for transport in transports.values())
    assert [job.__name__ for job in hass.executor_jobs] == [
        "_connect_and_discover",
        "_poll_devices",
    ]


@pytest.mark.asyncio
async def test_same_model_id_on_two_devices_remains_distinct() -> None:
    """Device snapshots disambiguate identical SunSpec model IDs."""
    registers_200, _ = model_chain((203, 105))
    registers_201, _ = model_chain((203, 105))
    coordinator = FroniusPVCoordinator(
        FakeHass(),
        FakeEntry({}),
        {200: FakeTransport(registers_200), 201: FakeTransport(registers_201)},
    )

    await coordinator.async_discover()
    data = await coordinator._async_update_data()

    identities = [
        (device.device_id, device.decoded_models[0].discovered.model_id)
        for device in data.devices
    ]
    assert identities == [
        (200, 203),
        (201, 203),
    ]


@pytest.mark.asyncio
async def test_partial_failure_keeps_other_device_snapshot_fresh() -> None:
    """One failed secondary device does not discard another device's data."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    inverter = FakeTransport(registers_1)
    meter = FakeTransport(registers_200)
    coordinator = FroniusPVCoordinator(
        FakeHass(), FakeEntry({}), {1: inverter, 200: meter}
    )
    await coordinator.async_discover()
    meter.fail_reads = True

    data = await coordinator._async_update_data()

    assert data.devices[0].available
    assert data.devices[0].decoded_models
    assert not data.devices[1].available
    assert data.devices[1].decoded_models == ()


@pytest.mark.asyncio
async def test_shared_endpoint_resets_after_first_device_and_recovers_second() -> None:
    """One failed view resets the session before the next device reconnects."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    inverter = FakeTransport(registers_1)
    meter = FakeTransport(registers_200)
    endpoint = FakeEndpoint({1: inverter, 200: meter})
    coordinator = FroniusPVCoordinator(
        FakeHass(),
        FakeEntry({}),
        {1: endpoint.bind(1), 200: endpoint.bind(200)},
    )
    await coordinator.async_discover()
    inverter.fail_reads = True

    data = await coordinator._async_update_data()

    assert not data.devices[0].available
    assert data.devices[0].decoded_models == ()
    assert data.devices[1].available
    assert data.devices[1].decoded_models
    assert endpoint.reset_calls == 1
    assert endpoint.connect_calls == 2


@pytest.mark.asyncio
async def test_shared_endpoint_still_fails_refresh_when_all_devices_fail() -> None:
    """Session recovery does not mask an overall endpoint refresh failure."""
    registers_1, _ = model_chain((103, 50))
    registers_200, _ = model_chain((203, 105))
    inverter = FakeTransport(registers_1)
    meter = FakeTransport(registers_200)
    endpoint = FakeEndpoint({1: inverter, 200: meter})
    coordinator = FroniusPVCoordinator(
        FakeHass(),
        FakeEntry({}),
        {1: endpoint.bind(1), 200: endpoint.bind(200)},
    )
    await coordinator.async_discover()
    inverter.fail_reads = meter.fail_reads = True

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert endpoint.reset_calls == 2


@pytest.mark.asyncio
async def test_close_attempts_every_owned_transport() -> None:
    """Runtime shutdown closes each configured device transport."""
    first = FakeTransport({})
    second = FakeTransport({})
    coordinator = FroniusPVCoordinator(
        FakeHass(), FakeEntry({}), {1: first, 200: second}
    )

    await coordinator.async_close()

    assert first.close_calls == 1
    assert second.close_calls == 1
