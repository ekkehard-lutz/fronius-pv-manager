"""Tests for the Home Assistant SunSpec runtime coordinator."""

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fronius_pv_manager.coordinator import FroniusPVCoordinator
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport, model_chain


def coordinator_with_models(*models: tuple[int, int]):
    """Create a coordinator and synthetic transport for selected model IDs."""
    registers, bases = model_chain(*models)
    hass = FakeHass()
    entry = FakeEntry({})
    transport = FakeTransport(registers)
    coordinator = FroniusPVCoordinator(hass, entry, transport)
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
        "_poll_models",
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
async def test_transport_failure_becomes_update_failed_and_later_recovers() -> None:
    """Coordinator availability fails on reads and recovers after a good refresh."""
    coordinator, _, transport, _ = coordinator_with_models((1, 65))
    await coordinator.async_discover()
    transport.fail_reads = True

    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()
    assert isinstance(raised.value.__cause__, ModbusTransportError)

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    transport.fail_reads = False
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.decoded_models[0].discovered.model_id == 1


@pytest.mark.asyncio
async def test_close_is_delegated_to_executor() -> None:
    """Persistent transport closure never runs directly in async coordinator code."""
    coordinator, hass, transport, _ = coordinator_with_models((1, 65))
    await coordinator.async_close()
    assert transport.close_calls == 1
    assert hass.executor_jobs[-1].__name__ == "close"
