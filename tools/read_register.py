"""Read one semantic SunSpec register without exposing write capability."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.fronius_pv_manager.const import (  # noqa: E402
    DEFAULT_PORT,
)
from custom_components.fronius_pv_manager.models import (  # noqa: E402
    get_help_text,
)
from custom_components.fronius_pv_manager.register_reader import (  # noqa: E402
    RegisterReadError,
    RegisterReadResult,
    read_register,
    resolve_readable_register,
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


def resolve_read_parameter(parameter: str):
    """Resolve one qualified fixed register or raise ValueError."""
    if parameter.count(":") != 1:
        raise ValueError("parameter must use MODEL_ID:NAME syntax")
    model_text, name = parameter.split(":", 1)
    if not model_text or not name:
        raise ValueError("parameter must use MODEL_ID:NAME syntax")
    try:
        model_id = int(model_text)
    except ValueError as err:
        raise ValueError(f"invalid model ID {model_text!r}") from err
    return resolve_readable_register(model_id, name)


def _display_raw(result: RegisterReadResult) -> str:
    """Format the combined raw value and its exact transport words."""
    raw = result.value.raw
    if isinstance(raw, int):
        width = result.register.size * 4
        return f"{raw} (0x{raw:0{width}X})"
    return ", ".join(f"0x{word:04X}" for word in result.raw_words)


def _print_result(
    result: RegisterReadResult,
    device_id: int,
    language: str,
    stdout: TextIO,
) -> None:
    """Print one decoded semantic register read."""
    definition = result.register
    current = "unavailable" if result.value.value is None else str(result.value.value)
    if definition.unit:
        current = f"{current} {definition.unit}"
    print(f"Device ID: {device_id}", file=stdout)
    print(f"Model: {result.model_id} - {result.model_name}", file=stdout)
    print(f"Parameter: {definition.name}", file=stdout)
    print(f"Access: {definition.access.value}", file=stdout)
    print(f"Current value: {current}", file=stdout)
    print(f"Raw value: {_display_raw(result)}", file=stdout)
    if result.scale_factor is not None:
        print(f"Scale factor: {result.scale_factor}", file=stdout)
    print(f"Unit: {definition.unit or 'none'}", file=stdout)
    print(f"Description: {definition.description or 'not available'}", file=stdout)
    if help_text := get_help_text(definition, language):
        print(f"Additional information: {help_text}", file=stdout)


def execute_register_read(
    host: str,
    port: int,
    device_id: int,
    parameter: str,
    *,
    language: str = "en",
    transport_factory: TransportFactory = ModbusTcpTransport,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Discover, read, and display one qualified semantic register."""
    try:
        match = resolve_read_parameter(parameter)
    except ValueError as err:
        print(f"Register read failed: {err}", file=stderr)
        return 1

    transport = transport_factory(host, port=port, device_id=device_id)
    connection_attempted = False
    status = 0
    try:
        connection_attempted = True
        transport.connect()
        models = SunSpecDiscovery(transport).discover()
        result = read_register(
            transport, models, match.model_id, match.register.name
        )
        _print_result(result, device_id, language, stdout)
    except (RegisterReadError, ModbusTransportError, SunSpecDiscoveryError) as err:
        print(f"Register read failed: {err}", file=stderr)
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
    """Create the read-only semantic register argument parser."""
    parser = argparse.ArgumentParser(
        description="Read one semantic SunSpec register without writing."
    )
    parser.add_argument("--host", required=True, help="Modbus TCP host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device-id", type=int, required=True)
    parser.add_argument("--parameter", required=True, help="Qualified MODEL_ID:NAME")
    parser.add_argument("--lang", default="en", help="Help-text language")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run one read-only register inspection."""
    arguments = create_argument_parser().parse_args(argv)
    return execute_register_read(
        arguments.host,
        arguments.port,
        arguments.device_id,
        arguments.parameter,
        language=arguments.lang,
    )


if __name__ == "__main__":
    raise SystemExit(main())
