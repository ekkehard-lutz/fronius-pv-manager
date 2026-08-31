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
from custom_components.fronius_pv_manager.model_decoder import (  # noqa: E402
    DecodedModel,
    decode_model,
)
from custom_components.fronius_pv_manager.models import (  # noqa: E402
    RegisterDataType,
    RegisterDefinition,
    RegisterValue,
    SunSpecModelDefinition,
    get_help_text,
)
from custom_components.fronius_pv_manager.register_maps import (  # noqa: E402
    RegisterLookup,
    find_registers,
    get_model_definition,
)
from custom_components.fronius_pv_manager.semantics import (  # noqa: E402
    augment_profile_with_model_160,
    classify_model_160_modules,
    infer_device_profile,
    physical_role_for_model,
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


def _format_mapping(mapping) -> str:
    """Format immutable numeric metadata mappings deterministically."""
    return ", ".join(f"{key}: {value}" for key, value in mapping.items())


def _print_parameter_info(
    match: RegisterLookup, stdout: TextIO, *, language: str = "en"
) -> None:
    """Print all available metadata for one exact register match."""
    register = match.register
    print(f"Model ID: {match.model_id}", file=stdout)
    print(f"Model name: {match.model.name}", file=stdout)
    print(f"Register name: {register.name}", file=stdout)
    if match.block_name is not None:
        print(f"Repeating block: {match.block_name}", file=stdout)
    print(f"Relative offset: {register.offset}", file=stdout)
    print(f"Data type: {register.data_type.value}", file=stdout)
    print(f"Access: {register.access.value}", file=stdout)
    print(f"Unit: {register.unit or 'none'}", file=stdout)
    print(f"Scale factor: {register.scale_factor or 'none'}", file=stdout)
    print(f"Polling class: {register.poll_class.value}", file=stdout)
    if register.valid_range is None:
        print("Valid range: none", file=stdout)
    else:
        value_range = register.valid_range
        print(
            f"Valid range: minimum={value_range.minimum}, "
            f"maximum={value_range.maximum}, step={value_range.step}",
            file=stdout,
        )
    print(
        f"Enum mappings: {_format_mapping(register.enum) if register.enum else 'none'}",
        file=stdout,
    )
    print(
        "Bitfield mappings: "
        f"{_format_mapping(register.bitfield) if register.bitfield else 'none'}",
        file=stdout,
    )
    if register.entity is None:
        print("Entity metadata: none", file=stdout)
    else:
        entity = register.entity
        print(f"Entity platform: {entity.platform.value}", file=stdout)
        print(f"Entity category: {entity.category.value}", file=stdout)
        print(f"Enabled by default: {entity.enabled_by_default}", file=stdout)
        role = entity.device_role.value if entity.device_role is not None else "none"
        print(f"Physical device role: {role}", file=stdout)
    print(f"Description: {register.description or 'not available'}", file=stdout)
    if help_text := get_help_text(register, language):
        print(f"Additional information: {help_text}", file=stdout)


def inspect_parameter(
    parameter: str,
    *,
    language: str = "en",
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Resolve and print metadata without constructing a Modbus transport."""
    model_id = None
    name = parameter
    if ":" in parameter:
        model_text, name = parameter.split(":", 1)
        try:
            model_id = int(model_text)
        except ValueError:
            print(
                f"Parameter lookup failed: invalid model ID {model_text!r}",
                file=stderr,
            )
            return 1
        if get_model_definition(model_id) is None:
            print(f"Parameter lookup failed: unknown model {model_id}", file=stderr)
            return 1

    matches = find_registers(name, model_id=model_id)
    if not matches:
        qualified = f"{model_id}:{name}" if model_id is not None else name
        print(f"Parameter lookup failed: unknown register {qualified}", file=stderr)
        return 1
    if model_id is None and len(matches) > 1:
        choices = ", ".join(
            f"{match.model_id}:{match.register.name}" for match in matches
        )
        print(
            f"Parameter name {name!r} is ambiguous; matches: {choices}. "
            "Use MODEL_ID:NAME.",
            file=stderr,
        )
        return 1
    _print_parameter_info(matches[0], stdout, language=language)
    return 0


def inspect_device(
    host: str,
    port: int,
    device_id: int,
    *,
    dump_model: int | None = None,
    decode: bool = False,
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

        if decode:
            _print_decoded_models(transport, models, stdout, stderr)

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


def _format_value(definition: RegisterDefinition, value: RegisterValue) -> str:
    """Format a decoded value without exposing codec implementation objects."""
    if value.value is None:
        return "unavailable"
    if definition.data_type in {
        RegisterDataType.BITFIELD16,
        RegisterDataType.BITFIELD32,
    } and value.value == "":
        return "none"
    return str(value.value)


def _print_register(
    definition: RegisterDefinition,
    value: RegisterValue,
    stdout: TextIO,
    *,
    indent: str = "  ",
) -> None:
    """Print one named decoded register and its optional engineering unit."""
    unit = f" {definition.unit}" if definition.unit else ""
    print(
        f"{indent}{definition.name:<20} {_format_value(definition, value)}{unit}",
        file=stdout,
    )


def _print_decoded_model(
    definition: SunSpecModelDefinition,
    decoded: DecodedModel,
    stdout: TextIO,
) -> None:
    """Print fixed and repeating values from an existing decoder result."""
    model_id = definition.model_ids[0]
    print(f"Model {model_id} - {definition.name}", file=stdout)
    role = physical_role_for_model(model_id)
    if role is not None:
        print(f"  physical role: {role.value}", file=stdout)
    for register in definition.registers:
        _print_register(register, decoded.fixed[register.name], stdout)

    classifications = (
        {
            item.instance_index: item
            for item in classify_model_160_modules(decoded)
        }
        if model_id == 160
        else {}
    )
    for block in definition.repeating_blocks:
        register_by_name = {register.name: register for register in block.registers}
        for instance in decoded.repeating.get(block.name, ()):
            print(
                f"  {block.name} instance {instance.instance_index}", file=stdout
            )
            classification = classifications.get(instance.instance_index)
            if classification is not None:
                print(
                    f"    semantic kind: {classification.semantic_kind.value}",
                    file=stdout,
                )
                physical_role = (
                    classification.physical_role.value
                    if classification.physical_role is not None
                    else "none"
                )
                print(f"    physical role: {physical_role}", file=stdout)
            for name, value in instance.values.items():
                _print_register(
                    register_by_name[name], value, stdout, indent="    "
                )


def _print_decoded_models(transport, models, stdout: TextIO, stderr: TextIO) -> None:
    """Read, decode, and format every locally supported discovered model."""
    profile = infer_device_profile(models)
    decoded_model_160 = None
    print("Decoded models:", file=stdout)
    for model in models:
        definition = get_model_definition(model.model_id)
        if definition is None:
            print(
                f"Model {model.model_id}: no local definition available; skipped",
                file=stdout,
            )
            continue
        if (
            definition.expected_length is not None
            and model.length != definition.expected_length
        ):
            print(
                f"Model {model.model_id}: discovered length {model.length} does not "
                f"match local expected length {definition.expected_length}; skipped",
                file=stderr,
            )
            continue
        payload = read_holding_registers_chunked(
            transport, model.base_address, model.length
        )
        try:
            decoded = decode_model(definition, payload)
        except ValueError as err:
            print(f"Model {model.model_id}: decoding failed: {err}", file=stderr)
            continue
        _print_decoded_model(definition, decoded, stdout)
        if model.model_id == 160:
            decoded_model_160 = decoded

    if decoded_model_160 is not None:
        profile = augment_profile_with_model_160(profile, decoded_model_160)
    print("Capabilities:", file=stdout)
    for capability in sorted(profile.capabilities, key=lambda item: item.value):
        print(f"  {capability.value}", file=stdout)


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Inspect the SunSpec model chain exposed by a Modbus TCP device."
    )
    parser.add_argument("--host", help="Modbus TCP host name or address")
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
    parser.add_argument(
        "--decode",
        action="store_true",
        help="Decode supported discovered models using local register maps",
    )
    parser.add_argument(
        "--info",
        metavar="PARAMETER",
        help="Show metadata for NAME or MODEL_ID:NAME without connecting",
    )
    parser.add_argument(
        "--lang",
        default="en",
        metavar="LANGUAGE",
        help="Language for --info additional text (default: en)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and run device inspection."""
    parser = create_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.info is not None:
        return inspect_parameter(arguments.info, language=arguments.lang)
    if arguments.host is None:
        parser.error("--host is required unless --info is used")
    return inspect_device(
        arguments.host,
        arguments.port,
        arguments.device_id,
        dump_model=arguments.dump_model,
        decode=arguments.decode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
