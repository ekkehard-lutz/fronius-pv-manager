"""Explicit minimum commands switch direction as one normalized HLC transition."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.fronius_pv_manager.storage_control import StorageControl
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_runtime import WriteSequenceError
from tests.control_entity_fakes import MODEL_BASE
from tests.test_storage_quantization import hardware, power_entities

DIRECTIONS = [
    (
        "minimum_charge_power",
        "minimum_discharge_power",
        "maximum_charge_power",
        {"StorCtl_Mod": 3, "InWRte": 100, "OutWRte": -9.77},
    ),
    (
        "minimum_discharge_power",
        "minimum_charge_power",
        "maximum_discharge_power",
        {"StorCtl_Mod": 3, "InWRte": -9.77, "OutWRte": 100},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["automatic", "manual"])
@pytest.mark.parametrize("field,opposite,maximum,target", DIRECTIONS)
async def test_positive_minimum_normalizes_once_and_persists(
    mode, field, opposite, maximum, target
):
    coordinator, control = hardware()
    await control.async_change(1, field=opposite, value=1500)
    await control.async_change(1, mode=mode)
    entities = await power_entities(coordinator)
    previous_target = deepcopy(control.last_targets)
    runtime = coordinator.write_runtime
    runtime.async_write_sequence = AsyncMock(wraps=runtime.async_write_sequence)
    start = len(coordinator.control_transport.write_calls)
    refreshes = coordinator.refresh_requests

    await entities[field].async_set_native_value(1000)

    assert entities[field].native_value == 1000
    assert entities[opposite].native_value == 0
    assert getattr(control.values(1), maximum) == 10240
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert getattr(restored.values(1), field) == 1000
    assert getattr(restored.values(1), opposite) == 0
    if mode == "manual":
        # One existing sequence, including its mandatory neutral preparation.
        # There is no separate semantic 'old direction off' sequence or refresh.
        runtime.async_write_sequence.assert_awaited_once_with(
            1,
            [
                (124, "StorCtl_Mod", 0),
                (124, "InWRte", 100),
                (124, "OutWRte", 100),
                (124, "InWRte", target["InWRte"]),
                (124, "OutWRte", target["OutWRte"]),
                (124, "StorCtl_Mod", 3),
            ],
        )
        assert [
            address - MODEL_BASE
            for address, _ in coordinator.control_transport.write_calls[start:]
        ] == [3, 11, 10, 11, 10, 3]
        assert coordinator.refresh_requests == refreshes + 1
        assert control.last_targets["1"] == target
        assert restored.last_targets["1"] == target
        assert control.status(1) == "manual_hlc"
    else:
        runtime.async_write_sequence.assert_not_awaited()
        assert coordinator.control_transport.write_calls[start:] == []
        assert coordinator.refresh_requests == refreshes
        assert control.last_targets == previous_target
        await control.async_change(1, mode="manual")
        assert control.last_targets["1"] == target


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["automatic", "manual"])
@pytest.mark.parametrize("field,opposite,maximum,target", DIRECTIONS)
async def test_zero_changes_only_requested_minimum(
    mode, field, opposite, maximum, target
):
    coordinator, control = hardware()
    await control.async_change(1, field=opposite, value=1500)
    await control.async_change(1, mode=mode)
    # Zeroing an already-zero field leaves the active opposite command intact.
    await control.async_change(1, field=field, value=0)
    assert getattr(control.values(1), opposite) == 1500
    # Switch direction, then clear it: the previous direction is not restored.
    await control.async_change(1, field=field, value=1000)
    await control.async_change(1, field=field, value=0)
    assert getattr(control.values(1), field) == 0
    assert getattr(control.values(1), opposite) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field,opposite,maximum,target", DIRECTIONS)
@pytest.mark.parametrize("value", [1000, 10241, -1, 1000.5])
async def test_invalid_change_does_not_clear_opposite_or_write(
    field, opposite, maximum, target, value
):
    coordinator, control = hardware()
    await control.async_change(1, field=maximum, value=500)
    await control.async_change(1, field=opposite, value=1500)
    await control.async_change(1, mode="manual")
    previous = deepcopy(control.settings)
    previous_target = deepcopy(control.last_targets)
    writes = list(coordinator.control_transport.write_calls)
    with pytest.raises(ServiceValidationError):
        await control.async_change(1, field=field, value=value)
    assert coordinator.control_transport.write_calls == writes
    assert control.settings == previous
    assert control.last_targets == previous_target


@pytest.mark.asyncio
@pytest.mark.parametrize("field,opposite,maximum,target", DIRECTIONS)
async def test_failed_direction_switch_preserves_previous_persisted_state(
    field, opposite, maximum, target
):
    coordinator, control = hardware()
    await control.async_change(1, field=opposite, value=1500)
    await control.async_change(1, mode="manual")
    previous = deepcopy(control.settings)
    previous_target = deepcopy(control.last_targets)
    refreshes = coordinator.refresh_requests
    original = coordinator.control_transport.write_holding_registers
    attempts = 0

    def write(address, words):
        nonlocal attempts
        attempts += 1
        if attempts == 5:
            raise ModbusTransportError("uncertain write")
        original(address, words)

    coordinator.control_transport.write_holding_registers = write
    with pytest.raises(WriteSequenceError) as error:
        await control.async_change(1, field=field, value=1000)
    assert len(error.value.completed) == 4
    assert attempts == 5
    assert coordinator.refresh_requests == refreshes
    assert control.settings == previous
    assert control.last_targets == previous_target
    restored = StorageControl(coordinator)
    await restored.async_load()
    assert restored.settings == previous
    assert restored.last_targets == previous_target
