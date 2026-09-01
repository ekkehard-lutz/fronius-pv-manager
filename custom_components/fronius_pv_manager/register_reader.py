"""Home Assistant-independent semantic reads for fixed SunSpec registers."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .codec import decode_register_value
from .models import DiscoveredModel, RegisterDefinition, RegisterValue
from .register_maps import RegisterLookup, find_registers, get_model_definition


class RegisterReadTransport(Protocol):
    """Transport operation required for a semantic register read."""

    def read_holding_registers(self, address: int, count: int) -> tuple[int, ...]:
        """Read a contiguous register range."""
        ...


class RegisterReadError(ValueError):
    """Raised when a semantic register cannot be resolved or decoded."""


@dataclass(frozen=True, slots=True)
class RegisterReadResult:
    """Immutable result of one live fixed-register read."""

    target: RegisterLookup
    discovered_model: DiscoveredModel
    transport_address: int
    value: RegisterValue
    raw_words: tuple[int, ...]
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
        """Return the target register definition."""
        return self.target.register


def resolve_readable_register(model_id: int, name: str) -> RegisterLookup:
    """Resolve one known fixed register from semantic coordinates."""
    if get_model_definition(model_id) is None:
        raise RegisterReadError(f"unknown model {model_id}")
    matches = find_registers(name, model_id=model_id)
    if not matches:
        raise RegisterReadError(f"unknown register {model_id}:{name}")
    target = matches[0]
    if target.block_name is not None:
        raise RegisterReadError(
            "repeating-block registers require an instance and are unsupported"
        )
    return target


def read_register(
    transport: RegisterReadTransport,
    discovered_models: Iterable[DiscoveredModel],
    model_id: int,
    register_name: str,
) -> RegisterReadResult:
    """Read and decode one known fixed register from a discovered model."""
    target = resolve_readable_register(model_id, register_name)
    discovered = next(
        (model for model in discovered_models if model.model_id == model_id), None
    )
    if discovered is None:
        raise RegisterReadError(f"model {model_id} is not present on the device")
    register = target.register
    if register.offset + register.size > discovered.length:
        raise RegisterReadError(
            "register does not fit within the discovered model length"
        )

    scale_factor = _read_scale_factor(transport, discovered, target)
    address = discovered.base_address + register.offset
    try:
        words = transport.read_holding_registers(address, register.size)
        value = decode_register_value(register, words, scale_factor)
    except ValueError as err:
        raise RegisterReadError(
            f"failed to decode current value for {model_id}:{register_name}"
        ) from err
    return RegisterReadResult(
        target,
        discovered,
        address,
        value,
        words,
        scale_factor,
    )


def _read_scale_factor(
    transport: RegisterReadTransport,
    discovered: DiscoveredModel,
    target: RegisterLookup,
) -> int | None:
    """Read a target's named scale factor when one is defined."""
    name = target.register.scale_factor
    if name is None:
        return None
    definition = next(
        (register for register in target.model.registers if register.name == name),
        None,
    )
    if definition is None:
        raise RegisterReadError(f"scale-factor register {name!r} is not defined")
    if definition.offset + definition.size > discovered.length:
        raise RegisterReadError(
            f"scale-factor register {name!r} does not fit within the discovered model"
        )
    try:
        words = transport.read_holding_registers(
            discovered.base_address + definition.offset, definition.size
        )
        decoded = decode_register_value(definition, words)
    except ValueError as err:
        raise RegisterReadError(f"failed to decode scale factor {name!r}") from err
    if type(decoded.value) is not int:
        raise RegisterReadError(f"scale factor {name!r} is unavailable or invalid")
    return decoded.value
