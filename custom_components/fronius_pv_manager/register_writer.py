"""Reusable Home Assistant-independent safe register write workflow."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from .codec import decode_register_value, encode_register_value
from .models import (
    DiscoveredModel,
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
)
from .register_maps import RegisterLookup, find_registers, get_model_definition
from .transport import ModbusTransportError


class RegisterWriteTransport(Protocol):
    """Transport operations required by register write preparation and execution."""

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read a contiguous register range."""
        ...

    def write_holding_registers(
        self, address: int, values: Sequence[int]
    ) -> None:
        """Write a contiguous register range."""
        ...


class RegisterWriteError(ValueError):
    """Raised when a semantic register write cannot be prepared or decoded."""


class RegisterWriteVerificationError(RegisterWriteError):
    """Raised when a completed write cannot be read back for verification."""


@dataclass(frozen=True, slots=True)
class PreparedRegisterWrite:
    """Immutable write plan prepared from current live device state.

    The address and scale factor are a live snapshot. Callers should prepare
    immediately before execution and must not cache or reinterpret this plan.
    """

    target: RegisterLookup
    discovered_model: DiscoveredModel
    transport_address: int
    current_value: RegisterValue
    requested_value: object
    encoded_words: tuple[int, ...]
    scale_factor: int | None

    @property
    def model_id(self) -> int:
        """Return the target SunSpec model ID."""
        return self.target.model_id

    @property
    def model_name(self) -> str:
        """Return the local model definition name."""
        return self.target.model.name

    @property
    def register(self) -> RegisterDefinition:
        """Return the immutable target register definition."""
        return self.target.register


@dataclass(frozen=True, slots=True)
class RegisterWriteResult:
    """Immutable semantic verification result for one executed plan."""

    prepared: PreparedRegisterWrite
    read_back: RegisterValue
    verified: bool


def resolve_writable_register(model_id: int, name: str) -> RegisterLookup:
    """Resolve one known fixed writable register from semantic coordinates."""
    if get_model_definition(model_id) is None:
        raise RegisterWriteError(f"unknown model {model_id}")
    matches = find_registers(name, model_id=model_id)
    if not matches:
        raise RegisterWriteError(f"unknown register {model_id}:{name}")
    target = matches[0]
    if target.block_name is not None:
        raise RegisterWriteError("repeating-block registers cannot be written")
    if target.register.access is not RegisterAccess.READ_WRITE:
        raise RegisterWriteError(f"register {model_id}:{name} is read-only")
    return target


def prepare_register_write(
    transport: RegisterWriteTransport,
    discovered_models: Iterable[DiscoveredModel],
    model_id: int,
    register_name: str,
    requested_value: object,
) -> PreparedRegisterWrite:
    """Perform all read-only work and return an immutable immediate write plan."""
    target = resolve_writable_register(model_id, register_name)
    discovered = next(
        (model for model in discovered_models if model.model_id == model_id), None
    )
    if discovered is None:
        raise RegisterWriteError(f"model {model_id} is not present on the device")
    register = target.register
    if register.offset + register.size > discovered.length:
        raise RegisterWriteError(
            "register does not fit within the discovered model length"
        )

    scale_factor = _resolve_scale_factor(transport, discovered, target)
    address = discovered.base_address + register.offset
    try:
        current = _read_value(transport, address, register, scale_factor)
    except ValueError as err:
        raise RegisterWriteError(
            f"failed to decode current value for {model_id}:{register_name}"
        ) from err
    try:
        words = encode_register_value(register, requested_value, scale_factor)
    except ValueError as err:
        raise RegisterWriteError(str(err)) from err
    return PreparedRegisterWrite(
        target=target,
        discovered_model=discovered,
        transport_address=address,
        current_value=current,
        requested_value=requested_value,
        encoded_words=words,
        scale_factor=scale_factor,
    )


def execute_register_write(
    transport: RegisterWriteTransport,
    prepared: PreparedRegisterWrite,
) -> RegisterWriteResult:
    """Write one prepared value once, read it once, and verify semantics."""
    transport.write_holding_registers(
        prepared.transport_address, prepared.encoded_words
    )
    try:
        read_back = _read_value(
            transport,
            prepared.transport_address,
            prepared.register,
            prepared.scale_factor,
        )
    except (ModbusTransportError, ValueError) as err:
        raise RegisterWriteVerificationError(
            "write succeeded but read-back failed"
        ) from err
    return RegisterWriteResult(
        prepared=prepared,
        read_back=read_back,
        verified=semantic_values_match(
            prepared.register, read_back, prepared.requested_value
        ),
    )


def semantic_values_match(
    definition: RegisterDefinition,
    read_back: RegisterValue,
    requested: object,
) -> bool:
    """Compare decoded semantics exactly without broad numeric tolerances."""
    if read_back.value is None:
        return False
    if definition.data_type in {
        RegisterDataType.ENUM16,
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        return read_back.raw == requested
    try:
        return Decimal(str(read_back.value)) == Decimal(str(requested))
    except InvalidOperation:
        return read_back.value == requested


def _read_value(
    transport: RegisterWriteTransport,
    address: int,
    definition: RegisterDefinition,
    scale_factor: int | None,
) -> RegisterValue:
    """Read and decode one fixed register at an absolute transport address."""
    words = transport.read_holding_registers(address, definition.size)
    return decode_register_value(definition, words, scale_factor)


def _resolve_scale_factor(
    transport: RegisterWriteTransport,
    discovered: DiscoveredModel,
    target: RegisterLookup,
) -> int | None:
    """Read and validate a target's named live scale-factor register."""
    name = target.register.scale_factor
    if name is None:
        return None
    scale_register = next(
        (register for register in target.model.registers if register.name == name), None
    )
    if scale_register is None:
        raise RegisterWriteError(f"scale-factor register {name!r} is not defined")
    if scale_register.offset + scale_register.size > discovered.length:
        raise RegisterWriteError(
            f"scale-factor register {name!r} does not fit within the discovered model"
        )
    try:
        decoded = _read_value(
            transport,
            discovered.base_address + scale_register.offset,
            scale_register,
            None,
        )
    except ValueError as err:
        raise RegisterWriteError(f"failed to decode scale factor {name!r}") from err
    if type(decoded.value) is not int:
        raise RegisterWriteError(
            f"scale factor {name!r} is unavailable or invalid"
        )
    return decoded.value
