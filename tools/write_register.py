"""Safely validate and test one writable SunSpec register on a live device."""

import argparse
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.fronius_pv_manager.const import (  # noqa: E402
    DEFAULT_PORT,
)
from custom_components.fronius_pv_manager.models import (  # noqa: E402
    RegisterDataType,
    RegisterDefinition,
    get_help_text,
)
from custom_components.fronius_pv_manager.register_writer import (  # noqa: E402
    PreparedRegisterWrite,
    RegisterWriteError,
    RegisterWriteVerificationError,
    prepare_register_write,
    resolve_writable_register,
)
from custom_components.fronius_pv_manager.register_writer import (  # noqa: E402
    execute_register_write as execute_prepared_write,
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


def resolve_write_parameter(parameter: str):
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
    return resolve_writable_register(model_id, name)


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


def _display_value(value: object, unit: str | None) -> str:
    """Format one semantic value and optional neutral engineering unit."""
    rendered = "unavailable" if value is None else str(value)
    return f"{rendered} {unit}" if unit else rendered


def _print_summary(
    prepared: PreparedRegisterWrite,
    device_id: int,
    language: str,
    stdout: TextIO,
) -> None:
    """Print the complete read-before-write summary."""
    definition = prepared.register
    print(f"Device ID: {device_id}", file=stdout)
    print(f"Model: {prepared.model_id} - {prepared.model_name}", file=stdout)
    print(f"Parameter: {definition.name}", file=stdout)
    print(f"Access: {definition.access.value}", file=stdout)
    print(f"Unit: {definition.unit or 'none'}", file=stdout)
    print(
        "Current value:   "
        f"{_display_value(prepared.current_value.value, definition.unit)}",
        file=stdout,
    )
    print(
        "Requested value: "
        f"{_display_value(prepared.requested_value, definition.unit)}",
        file=stdout,
    )
    print(
        "Writing raw:     "
        + ", ".join(f"0x{word:04X}" for word in prepared.encoded_words),
        file=stdout,
    )
    if prepared.scale_factor is not None:
        print(f"Scale factor: {prepared.scale_factor}", file=stdout)
    print(f"Description: {definition.description or 'not available'}", file=stdout)
    if help_text := get_help_text(definition, language):
        print(f"Additional information: {help_text}", file=stdout)


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
        prepared = prepare_register_write(
            transport,
            models,
            match.model_id,
            match.register.name,
            requested,
        )
        _print_summary(prepared, device_id, language, stdout)

        if not write:
            print("DRY RUN - no register was written.", file=stdout)
        elif not yes and input_function("Type YES to write this value: ") != "YES":
            print("Write aborted; no register was written.", file=stdout)
        else:
            try:
                result = execute_prepared_write(transport, prepared)
            except RegisterWriteVerificationError as err:
                print(
                    f"FAILED: {err}: {err.__cause__}",
                    file=stderr,
                )
                status = 1
            else:
                print(
                    "Read-back value: "
                    f"{_display_value(result.read_back.value, prepared.register.unit)}",
                    file=stdout,
                )
                if result.verified:
                    print("SUCCESS: read-back matches requested value.", file=stdout)
                else:
                    print(
                        "FAILED: read-back does not match requested value.",
                        file=stderr,
                    )
                    status = 1
    except (
        RegisterWriteError,
        ModbusTransportError,
        SunSpecDiscoveryError,
    ) as err:
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
