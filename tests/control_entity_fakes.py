"""Shared writable Model 124 runtime doubles for entity platform tests."""

from collections.abc import Mapping

from custom_components.fronius_pv_manager.coordinator import (
    DecodedModelSnapshot,
    DeviceSnapshot,
    FroniusPVCoordinator,
    FroniusPVCoordinatorData,
)
from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import DiscoveredModel
from custom_components.fronius_pv_manager.register_maps import MODEL_124
from custom_components.fronius_pv_manager.transport import ModbusTransportError
from custom_components.fronius_pv_manager.write_policy import WritePolicy
from tests.runtime_fakes import FakeEntry, FakeHass, FakeTransport

MODEL_BASE = 41000


def register(name):
    """Return one fixed Model 124 register definition."""
    return next(item for item in MODEL_124.registers if item.name == name)


class ControlTransport(FakeTransport):
    """Writable synthetic Model 124 transport with verification controls."""

    def __init__(self, *, update_after_write: bool = True) -> None:
        values = {MODEL_BASE + offset: 0 for offset in range(24)}
        values[MODEL_BASE + register("MinRsvPct").offset] = 700
        values[MODEL_BASE + register("MinRsvPct_SF").offset] = 0xFFFE
        super().__init__(values)
        self.update_after_write = update_after_write
        self.write_calls: list[tuple[int, tuple[int, ...]]] = []

    def write_holding_registers(self, address: int, values) -> None:
        """Record exactly one low-level write and optionally update live state."""
        words = tuple(values)
        self.write_calls.append((address, words))
        if self.update_after_write:
            for offset, word in enumerate(words):
                self.registers[address + offset] = word


class ControlCoordinator(FroniusPVCoordinator):
    """Coordinator that refreshes its snapshot from the writable fake transport."""

    def __init__(
        self,
        policies: Mapping[tuple[int, str], WritePolicy],
        *,
        transport: ControlTransport | None = None,
    ) -> None:
        self.control_transport = transport or ControlTransport()
        super().__init__(
            FakeHass(), FakeEntry({}), {1: self.control_transport}, policies
        )
        self.discovered_models_by_device = {
            1: (DiscoveredModel(124, MODEL_BASE, 24),)
        }
        self.refresh_requests = 0
        self.data = self._snapshot()
        self.last_update_success = True

    async def async_request_refresh(self) -> None:
        """Record refresh and publish only verified transport state."""
        self.refresh_requests += 1
        self.data = self._snapshot()

    def _snapshot(self) -> FroniusPVCoordinatorData:
        """Decode current synthetic transport words as confirmed coordinator data."""
        discovered = self.discovered_models_by_device[1][0]
        words = tuple(
            self.control_transport.registers[MODEL_BASE + offset]
            for offset in range(24)
        )
        decoded = DecodedModelSnapshot(
            discovered,
            MODEL_124,
            decode_model(MODEL_124, words),
        )
        return FroniusPVCoordinatorData(
            (DeviceSnapshot(1, (discovered,), (decoded,)),)
        )


class FailingReadBackTransport(ControlTransport):
    """Return unchanged state so semantic write verification fails."""

    def __init__(self) -> None:
        super().__init__(update_after_write=False)

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Retain ordinary reads and expose no transport-level failure."""
        try:
            return super().read_holding_registers(address, count)
        except KeyError as err:
            raise ModbusTransportError("missing synthetic register") from err
