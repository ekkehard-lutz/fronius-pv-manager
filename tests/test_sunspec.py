"""Tests for Home Assistant-independent SunSpec model discovery."""

import pytest

from custom_components.fronius_pv_manager.sunspec import (
    SUNSPEC_BASE_TRANSPORT_ADDRESS,
    SunSpecDiscovery,
    SunSpecDiscoveryError,
)


class FakeTransport:
    """Serve holding registers from an in-memory zero-based address map."""

    def __init__(self, registers: dict[int, int]) -> None:
        """Initialize the fake register map and read log."""
        self.registers = registers
        self.reads: list[tuple[int, int]] = []

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Return a contiguous register range and record its coordinates."""
        self.reads.append((address, count))
        return tuple(self.registers[address + offset] for offset in range(count))


def model_chain(*models: tuple[int, int]) -> dict[int, int]:
    """Build a synthetic SunSpec chain from model ID and length pairs."""
    registers = {
        SUNSPEC_BASE_TRANSPORT_ADDRESS: 0x5375,
        SUNSPEC_BASE_TRANSPORT_ADDRESS + 1: 0x6E53,
    }
    header_address = SUNSPEC_BASE_TRANSPORT_ADDRESS + 2
    for model_id, length in models:
        registers[header_address] = model_id
        registers[header_address + 1] = length
        header_address += 2 + length
    registers[header_address] = 0xFFFF
    registers[header_address + 1] = 0
    return registers


def test_valid_sunspec_signature() -> None:
    """The two signature words explicitly decode to ASCII SunS."""
    discovery = SunSpecDiscovery(FakeTransport(model_chain()))

    discovery.verify_signature()


def test_invalid_sunspec_signature() -> None:
    """Discovery rejects a base whose words do not decode to SunS."""
    registers = model_chain()
    registers[SUNSPEC_BASE_TRANSPORT_ADDRESS] = 0x0000
    discovery = SunSpecDiscovery(FakeTransport(registers))

    with pytest.raises(SunSpecDiscoveryError, match="signature"):
        discovery.discover()


def test_discovers_multiple_models_and_preserves_unknown_ids() -> None:
    """Discovery returns every model without requiring known definitions."""
    discovery = SunSpecDiscovery(
        FakeTransport(model_chain((1, 4), (103, 6), (65000, 3)))
    )

    models = discovery.discover()

    assert [model.model_id for model in models] == [1, 103, 65000]
    assert [model.length for model in models] == [4, 6, 3]


def test_model_base_addresses_progress_by_model_length() -> None:
    """Each data base follows the preceding model header and payload length."""
    transport = FakeTransport(model_chain((1, 4), (103, 6)))
    discovery = SunSpecDiscovery(transport)

    models = discovery.discover()

    first_base = SUNSPEC_BASE_TRANSPORT_ADDRESS + 4
    assert [model.base_address for model in models] == [first_base, first_base + 6]
    assert transport.reads == [
        (SUNSPEC_BASE_TRANSPORT_ADDRESS, 2),
        (SUNSPEC_BASE_TRANSPORT_ADDRESS + 2, 2),
        (first_base + 4, 2),
        (first_base + 12, 2),
    ]


def test_end_marker_stops_discovery() -> None:
    """The end marker terminates the chain without becoming a model."""
    models = SunSpecDiscovery(FakeTransport(model_chain((1, 4)))).discover()

    assert len(models) == 1
    assert models[0].model_id == 1


def test_zero_model_length_is_rejected() -> None:
    """A zero length cannot advance a valid model chain."""
    registers = model_chain()
    header_address = SUNSPEC_BASE_TRANSPORT_ADDRESS + 2
    registers[header_address] = 103
    registers[header_address + 1] = 0
    discovery = SunSpecDiscovery(FakeTransport(registers))

    with pytest.raises(SunSpecDiscoveryError, match="invalid length"):
        discovery.discover()


def test_model_progression_beyond_address_space_is_rejected() -> None:
    """A model cannot advance to a header that would cross address 0xFFFF."""
    start_address = 0xFFF9
    registers = {
        start_address: 0x5375,
        start_address + 1: 0x6E53,
        start_address + 2: 103,
        start_address + 3: 2,
    }
    discovery = SunSpecDiscovery(
        FakeTransport(registers), start_address=start_address
    )

    with pytest.raises(SunSpecDiscoveryError, match="address space"):
        discovery.discover()


def test_valid_header_near_address_space_end_is_accepted() -> None:
    """A two-register header ending at 0xFFFF remains readable and valid."""
    start_address = 0xFFF9
    registers = {
        start_address: 0x5375,
        start_address + 1: 0x6E53,
        start_address + 2: 103,
        start_address + 3: 1,
        0xFFFE: 0xFFFF,
        0xFFFF: 0,
    }
    transport = FakeTransport(registers)
    discovery = SunSpecDiscovery(transport, start_address=start_address)

    models = discovery.discover()

    assert len(models) == 1
    assert models[0].base_address == 0xFFFD
    assert transport.reads[-1] == (0xFFFE, 2)


def test_maximum_model_limit_is_enforced() -> None:
    """A malformed chain cannot make discovery walk without a bound."""
    discovery = SunSpecDiscovery(
        FakeTransport(model_chain((1, 1), (2, 1))), max_models=2
    )

    with pytest.raises(SunSpecDiscoveryError, match="limit of 2"):
        discovery.discover()
