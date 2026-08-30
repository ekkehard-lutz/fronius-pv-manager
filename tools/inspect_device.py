"""Inspect a device's SunSpec model chain for development diagnostics."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

# Support the documented ``python tools/inspect_device.py`` invocation from a
# source checkout without requiring the integration to be installed as a package.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.fronius_pv_manager.const import (  # noqa: E402
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
)
from custom_components.fronius_pv_manager.sunspec import (  # noqa: E402
    SunSpecDiscovery,
    SunSpecDiscoveryError,
)
from custom_components.fronius_pv_manager.transport import (  # noqa: E402
    ModbusTcpTransport,
    ModbusTransportError,
    read_holding_registers_chunked,
)

TransportFactory = Callable[..., ModbusTcpTransport]


def inspect_device(
    host: str,
    port: int,
    device_id: int,
    *,
    dump_model: int | None = None,
    transport_factory: TransportFactory = ModbusTcpTransport,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Connect, discover the SunSpec model chain, and print a summary."""
    transport = transport_factory(host, port=port, device_id=device_id)
    connection_attempted = False
    exit_status = 0

    try:
        connection_attempted = True
        transport.connect()
        print(f"Host: {host}", file=stdout)
        print(f"Port: {port}", file=stdout)
        print(f"Device ID: {device_id}", file=stdout)
        print("Connection: successful", file=stdout)

        models = SunSpecDiscovery(transport).discover()
        print("SunSpec signature: valid", file=stdout)
        print("Discovered models:", file=stdout)
        for model in models:
            documentation_register = model.base_address + 1
            print(
                f"  Model {model.model_id}: length={model.length}, "
                f"zero-based transport data address={model.base_address}, "
                f"one-based documentation register={documentation_register}",
                file=stdout,
            )
        print(f"Discovered model count: {len(models)}", file=stdout)

        if dump_model is not None:
            selected_model = next(
                (model for model in models if model.model_id == dump_model), None
            )
            if selected_model is None:
                print(
                    f"Inspection failed: SunSpec model {dump_model} was not found",
                    file=stderr,
                )
                exit_status = 1
            else:
                registers = read_holding_registers_chunked(
                    transport,
                    selected_model.base_address,
                    selected_model.length,
                )
                print(f"Model {dump_model} raw payload:", file=stdout)
                print("Offset  Transport  Register  Decimal  Hex", file=stdout)
                for offset, value in enumerate(registers):
                    transport_address = selected_model.base_address + offset
                    documentation_register = transport_address + 1
                    print(
                        f"{offset:6d}  {transport_address:9d}  "
                        f"{documentation_register:8d}  {value:7d}  0x{value:04X}",
                        file=stdout,
                    )
    except (ModbusTransportError, SunSpecDiscoveryError) as err:
        print(f"Inspection failed: {err}", file=stderr)
        exit_status = 1
    finally:
        if connection_attempted:
            try:
                transport.close()
            except ModbusTransportError as err:
                print(f"Failed to close transport: {err}", file=stderr)
                exit_status = 1

    return exit_status


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Inspect the SunSpec model chain exposed by a Modbus TCP device."
    )
    parser.add_argument("--host", required=True, help="Modbus TCP host name or address")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Modbus TCP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=DEFAULT_UNIT_ID,
        help=f"Modbus device ID (default: {DEFAULT_UNIT_ID})",
    )
    parser.add_argument(
        "--dump-model",
        type=int,
        metavar="MODEL_ID",
        help="Dump the raw payload of a discovered SunSpec model",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and run device inspection."""
    arguments = create_argument_parser().parse_args(argv)
    return inspect_device(
        arguments.host,
        arguments.port,
        arguments.device_id,
        dump_model=arguments.dump_model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
