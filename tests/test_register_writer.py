"""Tests for the reusable Home Assistant-independent register write service."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.fronius_pv_manager.models import DiscoveredModel
from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.register_writer import (
    RegisterWriteError,
    RegisterWriteVerificationError,
    execute_register_write,
    prepare_register_write,
    resolve_writable_register,
)
from custom_components.fronius_pv_manager.transport import ModbusTransportError

MODEL_BASE = 41000


def register(model, name):
    """Find one fixed register definition by name."""
    return next(definition for definition in model.registers if definition.name == name)


class FakeWriteTransport:
    """Record core reads/writes and simulate live register state."""

    def __init__(
        self,
        registers: dict[int, int],
        *,
        update_after_write: bool = True,
        write_error: bool = False,
        read_back_error: bool = False,
    ) -> None:
        self.registers = registers
        self.update_after_write = update_after_write
        self.write_error = write_error
        self.read_back_error = read_back_error
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, tuple[int, ...]]] = []

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        self.reads.append((address, count))
        if self.read_back_error and self.writes:
            raise ModbusTransportError("read-back failed")
        return tuple(self.registers[address + offset] for offset in range(count))

    def write_holding_registers(self, address: int, values) -> None:
        if self.write_error:
            raise ModbusTransportError("write failed")
        words = tuple(values)
        self.writes.append((address, words))
        if self.update_after_write:
            for offset, word in enumerate(words):
                self.registers[address + offset] = word


def storage_transport(**kwargs) -> FakeWriteTransport:
    """Provide live values for representative Model 124 writes."""
    values = {MODEL_BASE + offset: 0 for offset in range(24)}
    values[MODEL_BASE + register(MODEL_124, "MinRsvPct_SF").offset] = 0xFFFE
    values[MODEL_BASE + register(MODEL_124, "MinRsvPct").offset] = 700
    return FakeWriteTransport(values, **kwargs)


def discovered(model_id: int = 124, length: int = 24) -> tuple[DiscoveredModel, ...]:
    """Create one immutable discovered-model collection."""
    return (DiscoveredModel(model_id, MODEL_BASE, length),)


def prepare_storage(
    transport: FakeWriteTransport, value=10
):
    """Prepare a representative scaled storage reserve write."""
    return prepare_register_write(
        transport, discovered(), 124, "MinRsvPct", value
    )


def test_writable_resolution_accepts_fixed_and_rejects_unsafe_targets() -> None:
    """Core semantic lookup rejects unknown, read-only, and repeating targets."""
    assert resolve_writable_register(124, "MinRsvPct").register.name == "MinRsvPct"
    for model_id, name, message in (
        (999, "W", "unknown model"),
        (124, "Missing", "unknown register"),
        (124, "ChaState", "read-only"),
        (160, "DCW", "repeating-block"),
    ):
        with pytest.raises(RegisterWriteError, match=message):
            resolve_writable_register(model_id, name)


def test_preparation_rejects_missing_or_structurally_short_model() -> None:
    """Live discovery must contain the complete fixed target definition."""
    transport = storage_transport()
    with pytest.raises(RegisterWriteError, match="not present"):
        prepare_register_write(transport, discovered(103, 50), 124, "MinRsvPct", 10)
    with pytest.raises(RegisterWriteError, match="does not fit"):
        prepare_register_write(transport, discovered(length=5), 124, "MinRsvPct", 10)


def test_preparation_reads_live_scale_current_and_encodes_without_writing() -> None:
    """Preparation captures all live state but remains strictly read-only."""
    transport = storage_transport()
    prepared = prepare_storage(transport)
    scale_address = MODEL_BASE + register(MODEL_124, "MinRsvPct_SF").offset
    target_address = MODEL_BASE + register(MODEL_124, "MinRsvPct").offset

    assert prepared.model_id == 124
    assert prepared.model_name == "Storage"
    assert prepared.discovered_model == discovered()[0]
    assert prepared.transport_address == target_address
    assert prepared.scale_factor == -2
    assert prepared.current_value.value == 7
    assert prepared.requested_value == 10
    assert prepared.encoded_words == (1000,)
    assert transport.reads == [(scale_address, 1), (target_address, 1)]
    assert transport.writes == []
    with pytest.raises(FrozenInstanceError):
        prepared.scale_factor = 0  # type: ignore[misc]


def test_preparation_rejects_unavailable_scale_factor() -> None:
    """Preparation never substitutes documentation for an invalid live SUNSSF."""
    transport = storage_transport()
    address = MODEL_BASE + register(MODEL_124, "MinRsvPct_SF").offset
    transport.registers[address] = 0x8000
    with pytest.raises(RegisterWriteError, match="unavailable or invalid"):
        prepare_storage(transport)
    assert transport.writes == []


def test_execution_writes_plan_once_reads_once_and_uses_prepared_scale() -> None:
    """Execution consumes the immutable address, words, and scale snapshot exactly."""
    transport = storage_transport()
    prepared = prepare_storage(transport)
    transport.reads.clear()
    scale_address = MODEL_BASE + register(MODEL_124, "MinRsvPct_SF").offset
    transport.registers[scale_address] = 0

    result = execute_register_write(transport, prepared)

    assert transport.writes == [
        (prepared.transport_address, prepared.encoded_words)
    ]
    assert transport.reads == [(prepared.transport_address, prepared.register.size)]
    assert result.prepared is prepared
    assert result.read_back.value == 10
    assert result.verified


def test_numeric_mismatch_and_unavailable_read_back_do_not_verify() -> None:
    """Unchanged or unavailable decoded semantics cannot verify a write."""
    mismatch = storage_transport(update_after_write=False)
    mismatch_result = execute_register_write(mismatch, prepare_storage(mismatch))
    assert not mismatch_result.verified

    unavailable = storage_transport(update_after_write=False)
    target = MODEL_BASE + register(MODEL_124, "MinRsvPct").offset
    unavailable.registers[target] = 0xFFFF
    unavailable_result = execute_register_write(
        unavailable, prepare_storage(unavailable)
    )
    assert unavailable_result.read_back.value is None
    assert not unavailable_result.verified


def test_enum_and_bitfield_semantic_verification() -> None:
    """Protocol choices and masks verify through canonical decoded raw semantics."""
    enum_transport = storage_transport()
    enum_prepared = prepare_register_write(
        enum_transport, discovered(), 124, "ChaGriSet", 1
    )
    assert execute_register_write(enum_transport, enum_prepared).verified

    bitfield_values = {MODEL_BASE + offset: 0 for offset in range(24)}
    bitfield_transport = FakeWriteTransport(bitfield_values)
    bitfield_prepared = prepare_register_write(
        bitfield_transport,
        discovered(123, 24),
        123,
        "Conn",
        1,
    )
    assert execute_register_write(bitfield_transport, bitfield_prepared).verified


def test_execution_surfaces_write_and_read_back_transport_failures() -> None:
    """Write failures remain transport errors and read-back failures are distinct."""
    write_failure = storage_transport(write_error=True)
    with pytest.raises(ModbusTransportError, match="write failed"):
        execute_register_write(write_failure, prepare_storage(write_failure))

    read_failure = storage_transport(read_back_error=True)
    with pytest.raises(RegisterWriteVerificationError) as raised:
        execute_register_write(read_failure, prepare_storage(read_failure))
    assert isinstance(raised.value.__cause__, ModbusTransportError)
