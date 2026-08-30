"""Home Assistant-independent SunSpec model-chain discovery.

SunSpec documentation register 40001 is the default location of the two-register
``SunS`` signature. pymodbus uses zero-based transport addresses, so this module
converts that one documentation number once to transport address 40000. All
subsequent discovery arithmetic is performed solely in transport addresses.

Each model begins with a two-register ID/length header. The returned
``DiscoveredModel.base_address`` is the zero-based transport address of the
model's first data register, which is also the base to which model-relative
``RegisterDefinition.offset`` values will later be applied.
"""

from typing import Protocol

from .models import DiscoveredModel

SUNSPEC_DOCUMENTATION_BASE_REGISTER = 40001
SUNSPEC_BASE_TRANSPORT_ADDRESS = SUNSPEC_DOCUMENTATION_BASE_REGISTER - 1
SUNSPEC_SIGNATURE = b"SunS"
SUNSPEC_END_MODEL_ID = 0xFFFF
SUNSPEC_SIGNATURE_SIZE = 2
SUNSPEC_MODEL_HEADER_SIZE = 2
MAX_MODBUS_ADDRESS = 0xFFFF
DEFAULT_MAX_MODELS = 256


class HoldingRegisterTransport(Protocol):
    """Transport behavior required by SunSpec discovery."""

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read holding registers from a zero-based transport address."""
        ...


class SunSpecDiscoveryError(Exception):
    """Raised when a device does not expose a valid SunSpec model chain."""


class SunSpecDiscovery:
    """Verify the SunSpec signature and walk a device's model chain."""

    def __init__(
        self,
        transport: HoldingRegisterTransport,
        *,
        start_address: int = SUNSPEC_BASE_TRANSPORT_ADDRESS,
        max_models: int = DEFAULT_MAX_MODELS,
    ) -> None:
        """Initialize discovery with zero-based transport coordinates."""
        if start_address < 0:
            raise ValueError("start_address must not be negative")
        if max_models <= 0:
            raise ValueError("max_models must be positive")
        self._transport = transport
        self._start_address = start_address
        self._max_models = max_models

    def verify_signature(self) -> None:
        """Verify that the two base registers explicitly decode to ASCII SunS."""
        registers = self._transport.read_holding_registers(
            self._start_address, SUNSPEC_SIGNATURE_SIZE
        )
        try:
            signature = b"".join(
                register.to_bytes(2, byteorder="big") for register in registers
            )
        except (AttributeError, OverflowError) as err:
            raise SunSpecDiscoveryError("invalid SunSpec signature registers") from err
        if signature != SUNSPEC_SIGNATURE:
            raise SunSpecDiscoveryError("invalid SunSpec signature")

    def discover(self) -> tuple[DiscoveredModel, ...]:
        """Return every model in the chain, preserving unknown model IDs."""
        self.verify_signature()
        header_address = self._start_address + SUNSPEC_SIGNATURE_SIZE
        models: list[DiscoveredModel] = []

        for _ in range(self._max_models):
            if header_address > MAX_MODBUS_ADDRESS - 1:
                raise SunSpecDiscoveryError("model header exceeds Modbus address space")
            model_id, length = self._transport.read_holding_registers(
                header_address, SUNSPEC_MODEL_HEADER_SIZE
            )
            if model_id == SUNSPEC_END_MODEL_ID:
                return tuple(models)
            if length <= 0:
                raise SunSpecDiscoveryError(
                    f"model {model_id} has invalid length {length}"
                )

            base_address = header_address + SUNSPEC_MODEL_HEADER_SIZE
            next_header_address = base_address + length
            if next_header_address > MAX_MODBUS_ADDRESS - 1:
                raise SunSpecDiscoveryError(
                    f"model {model_id} length exceeds Modbus address space"
                )
            models.append(
                DiscoveredModel(
                    model_id=model_id,
                    base_address=base_address,
                    length=length,
                )
            )
            header_address = next_header_address

        raise SunSpecDiscoveryError(
            f"SunSpec model chain exceeds limit of {self._max_models} models"
        )
