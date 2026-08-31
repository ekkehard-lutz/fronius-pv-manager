"""Safely validate and test one writable SunSpec register on a live device."""

import argparse
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.fronius_pv_manager.codec import (  # noqa: E402
    decode_register_value,
    encode_register_value,
)
from custom_components.fronius_pv_manager.const import (  # noqa: E402
    DEFAULT_PORT,
)
from custom_components.fronius_pv_manager.models import (  # noqa: E402
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
    get_help_text,
)
from custom_components.fronius_pv_manager.register_maps import (  # noqa: E402
    RegisterLookup,
    find_registers,
    get_model_definition,
)
from custom_components.fronius_pv_manager.sunspec import (  # noqa: E402
    SunSpecDiscovery,
    SunSpecDiscoveryError,
)
from custom_components.fronius_pv_manager.transport import (  # noqa: E402
    ModbusTcpTransport,
    ModbusTransportError,
)

TransportFactory = Callable[..., ModbusTcpTransport]
InputFunction = Callable[[str], str]


def resolve_write_parameter(parameter: str) -> RegisterLookup:
    """Resolve one qualified, fixed, writable register or raise ValueError."""
    if parameter.count(":") != 1:
        raise ValueError("parameter must use MODEL_ID:NAME syntax")
    model_text, name = parameter.split(":", 1)
    if not model_text or not name:
        raise ValueError("parameter must use MODEL_ID:NAME syntax")
    try:
        model_id = int(model_text)
    except ValueError as err:
        raise ValueError(f"invalid model ID {model_text!r}") from err
    if get_model_definition(model_id) is None:
        raise ValueError(f"unknown model {model_id}")
    matches = find_registers(name, model_id=model_id)
    if not matches:
        raise ValueError(f"unknown register {model_id}:{name}")
    match = matches[0]
    if match.block_name is not None:
        raise ValueError("repeating-block registers cannot be written")
    if match.register.access is not RegisterAccess.READ_WRITE:
        raise ValueError(f"register {model_id}:{name} is read-only")
    return match


def parse_requested_value(definition: RegisterDefinition, text: str) -> object:
    """Parse CLI text according to the selected register's data type."""
    if definition.data_type is RegisterDataType.ENUM16:
        if definition.enum is not None:
            labels = [key for key, label in definition.enum.items() if label == text]
            if len(labels) == 1:
                return labels[0]
        try:
            return int(text, 10)
        except ValueError as err:
            raise ValueError(
                "enum value must be a numeric choice or exact label"
            ) from err
    if definition.data_type in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    }:
        try:
            return int(text, 0)
        except ValueError as err:
            raise ValueError("bitfield value must be an integer mask") from err
    try:
        return Decimal(text)
    except InvalidOperation as err:
        raise ValueError("value must be a decimal number") from err


def _read_value(
    transport: ModbusTcpTransport,
    address: int,
    definition: RegisterDefinition,
    scale_factor: int | None,
) -> RegisterValue:
    """Read and decode one fixed register at an absolute transport address."""
    words = transport.read_holding_registers(address, definition.size)
    return decode_register_value(definition, words, scale_factor)


def _resolve_scale_factor(
    transport: ModbusTcpTransport,
    model_base: int,
    match: RegisterLookup,
) -> int | None:
    """Read a named fixed scale factor from the live discovered model."""
    name = match.register.scale_factor
    if name is None:
        return None
    scale_register = next(
        (register for register in match.model.registers if register.name == name), None
    )
    if scale_register is None:
        raise ValueError(f"scale-factor register {name!r} is not defined")
    decoded = _read_value(
        transport, model_base + scale_register.offset, scale_register, None
    )
    if type(decoded.value) is not int:
        raise ValueError(f"scale factor {name!r} is unavailable or invalid")
    return decoded.value


def _display_value(value: object, unit: str | None) -> str:
    """Format one semantic value and optional neutral engineering unit."""
    rendered = "unavailable" if value is None else str(value)
    return f"{rendered} {unit}" if unit else rendered


def _print_summary(
    match: RegisterLookup,
    device_id: int,
    current: RegisterValue,
    requested: object,
    words: tuple[int, ...],
    scale_factor: int | None,
    language: str,
    stdout: TextIO,
) -> None:
    """Print the complete read-before-write summary."""
    definition = match.register
    print(f"Device ID: {device_id}", file=stdout)
    print(f"Model: {match.model_id} - {match.model.name}", file=stdout)
    print(f"Parameter: {definition.name}", file=stdout)
    print(f"Access: {definition.access.value}", file=stdout)
    print(f"Unit: {definition.unit or 'none'}", file=stdout)
    print(
        f"Current value:   {_display_value(current.value, definition.unit)}",
        file=stdout,
    )
    print(
        f"Requested value: {_display_value(requested, definition.unit)}",
        file=stdout,
    )
    print(
        "Writing raw:     " + ", ".join(f"0x{word:04X}" for word in words),
        file=stdout,
    )
    if scale_factor is not None:
        print(f"Scale factor: {scale_factor}", file=stdout)
    print(f"Description: {definition.description or 'not available'}", file=stdout)
    if help_text := get_help_text(definition, language):
        print(f"Additional information: {help_text}", file=stdout)


def _semantic_matches(
    definition: RegisterDefinition,
    read_back: RegisterValue,
    requested: object,
) -> bool:
    """Compare decoded semantics exactly, using canonical numeric protocol choices."""
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


def execute_register_write(
    host: str,
    port: int,
    device_id: int,
    parameter: str,
    value_text: str,
    *,
    write: bool = False,
    yes: bool = False,
    language: str = "en",
    transport_factory: TransportFactory = ModbusTcpTransport,
    input_function: InputFunction = input,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Read, validate, optionally write, and verify one qualified register."""
    try:
        match = resolve_write_parameter(parameter)
        requested = parse_requested_value(match.register, value_text)
    except ValueError as err:
        print(f"Write test failed: {err}", file=stderr)
        return 1

    transport = transport_factory(host, port=port, device_id=device_id)
    connection_attempted = False
    status = 0
    try:
        connection_attempted = True
        transport.connect()
        models = SunSpecDiscovery(transport).discover()
        discovered = next(
            (model for model in models if model.model_id == match.model_id), None
        )
        if discovered is None:
            raise ValueError(f"model {match.model_id} is not present on the device")
        if match.register.offset + match.register.size > discovered.length:
            raise ValueError("register does not fit within the discovered model length")

        scale_factor = _resolve_scale_factor(
            transport, discovered.base_address, match
        )
        address = discovered.base_address + match.register.offset
        current = _read_value(transport, address, match.register, scale_factor)
        words = encode_register_value(match.register, requested, scale_factor)
        _print_summary(
            match,
            device_id,
            current,
            requested,
            words,
            scale_factor,
            language,
            stdout,
        )

        if not write:
            print("DRY RUN - no register was written.", file=stdout)
        elif not yes and input_function("Type YES to write this value: ") != "YES":
            print("Write aborted; no register was written.", file=stdout)
        else:
            transport.write_holding_registers(address, words)
            try:
                read_back = _read_value(
                    transport, address, match.register, scale_factor
                )
            except (ModbusTransportError, ValueError) as err:
                print(
                    f"FAILED: write succeeded but read-back failed: {err}",
                    file=stderr,
                )
                status = 1
            else:
                print(
                    "Read-back value: "
                    f"{_display_value(read_back.value, match.register.unit)}",
                    file=stdout,
                )
                if _semantic_matches(match.register, read_back, requested):
                    print("SUCCESS: read-back matches requested value.", file=stdout)
                else:
                    print(
                        "FAILED: read-back does not match requested value.",
                        file=stderr,
                    )
                    status = 1
    except (ValueError, ModbusTransportError, SunSpecDiscoveryError) as err:
        print(f"Write test failed: {err}", file=stderr)
        status = 1
    finally:
        if connection_attempted:
            try:
                transport.close()
            except ModbusTransportError as err:
                print(f"Failed to close transport: {err}", file=stderr)
                status = 1
    return status


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the safe single-register write-test argument parser."""
    parser = argparse.ArgumentParser(
        description="Safely test one writable SunSpec register (dry run by default)."
    )
    parser.add_argument("--host", required=True, help="Modbus TCP host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device-id", type=int, required=True)
    parser.add_argument("--parameter", required=True, help="Qualified MODEL_ID:NAME")
    parser.add_argument("--value", required=True, help="Requested semantic value")
    parser.add_argument("--write", action="store_true", help="Perform the write")
    parser.add_argument(
        "--yes", action="store_true", help="Skip interactive confirmation with --write"
    )
    parser.add_argument("--lang", default="en", help="Help-text language")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run one safe register write test."""
    arguments = create_argument_parser().parse_args(argv)
    return execute_register_write(
        arguments.host,
        arguments.port,
        arguments.device_id,
        arguments.parameter,
        arguments.value,
        write=arguments.write,
        yes=arguments.yes,
        language=arguments.lang,
    )


if __name__ == "__main__":
    raise SystemExit(main())
