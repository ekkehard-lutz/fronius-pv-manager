"""JSON-safe entity topology, containing identity metadata but no measurements."""

import logging
from dataclasses import asdict

from .model_decoder import DecodedModel, DecodedRepeatingBlockInstance
from .models import DiscoveredModel, RegisterValue

CONF_TOPOLOGY = "topology"
_LOGGER = logging.getLogger(__name__)


def model_topology(discovered, decoded=None):
    """Keep model coordinates and only the identity needed to build entities."""
    result = {"model": asdict(discovered), "fixed": {}, "repeating": {}}
    if decoded is None:
        return result
    if discovered.model_id == 1:
        result["fixed"] = {
            name: decoded.fixed[name].value for name in ("Mn", "Md", "SN", "Vr")
        }
    for name, instances in decoded.repeating.items():
        result["repeating"][name] = [
            {
                "instance_index": instance.instance_index,
                "base_offset": instance.base_offset,
                "values": {
                    key: value.value
                    for key, value in instance.values.items()
                    if key in {"ID", "IDStr"}
                },
            }
            for instance in instances
        ]
    return result


def restore_model(record):
    """Restore structural descriptors; these must never become live data."""

    def values(items):
        return {
            key: RegisterValue(raw=value, value=value) for key, value in items.items()
        }

    return DiscoveredModel(**record["model"]), DecodedModel(
        fixed=values(record["fixed"]),
        repeating={
            name: tuple(
                DecodedRepeatingBlockInstance(
                    instance_index=item["instance_index"],
                    base_offset=item["base_offset"],
                    values=values(item["values"]),
                )
                for item in instances
            )
            for name, instances in record["repeating"].items()
        },
    )


def valid_device_key(value):
    """Accept canonical persisted Modbus IDs, without bool/float coercion."""
    return (
        isinstance(value, str)
        and len(value) <= 3
        and value.isascii()
        and value.isdecimal()
        and 1 <= int(value) <= 247
        and str(int(value)) == value
    )


def validate_topology(saved):
    """Discard damaged structural records without making them live authority."""
    if not isinstance(saved, dict):
        _LOGGER.warning("Ignoring malformed topology; live discovery will rebuild it")
        return {}
    clean = {}
    for device, records in saved.items():
        if not valid_device_key(device) or not isinstance(records, list):
            _LOGGER.warning("Ignoring malformed topology device %r", device)
            continue
        accepted = []
        seen = set()
        for record in records:
            try:
                normalized = _validate_record(record)
                address = normalized["model"]["base_address"]
                if address in seen:
                    raise ValueError("duplicate model address")
                seen.add(address)
                accepted.append(normalized)
            except KeyError, TypeError, ValueError:
                _LOGGER.warning(
                    "Ignoring malformed topology record for device %s; "
                    "live discovery will rebuild it",
                    device,
                )
        if accepted:
            clean[device] = accepted
    return clean


def _validate_record(record):
    from .register_maps import get_model_definition

    model = record["model"]
    if set(model) != {"model_id", "base_address", "length"} or any(
        type(value) is not int for value in model.values()
    ):
        raise ValueError("invalid model coordinates")
    discovered = DiscoveredModel(**model)
    if not 1 <= discovered.model_id < 65535 or (
        discovered.base_address + discovered.length > 65536
    ):
        raise ValueError("model outside Modbus address space")
    fixed, repeating = record["fixed"], record["repeating"]
    if not isinstance(fixed, dict) or not isinstance(repeating, dict):
        raise ValueError("invalid identity containers")
    result = model_topology(discovered)
    if discovered.model_id == 1 and fixed:
        for key in ("Mn", "Md", "SN", "Vr"):
            value = fixed.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError("invalid identity string")
            result["fixed"][key] = value
    definition = get_model_definition(discovered.model_id)
    blocks = (
        {block.name: block for block in definition.repeating_blocks}
        if definition
        else {}
    )
    for name, instances in repeating.items():
        if name not in blocks or not isinstance(instances, list):
            raise ValueError("invalid repeating block")
        block = blocks[name]
        clean = []
        for index, item in enumerate(instances):
            offset = block.offset + index * block.block_size
            if (
                type(item["instance_index"]) is not int
                or item["instance_index"] != index
                or type(item["base_offset"]) is not int
                or item["base_offset"] != offset
                or offset + block.block_size > discovered.length
                or not isinstance(item["values"], dict)
            ):
                raise ValueError("invalid repeating coordinates")
            values = item["values"]
            if values.get("ID") is not None and (
                type(values["ID"]) is not int or not 0 <= values["ID"] <= 65535
            ):
                raise ValueError("invalid module ID")
            if values.get("IDStr") is not None and not isinstance(values["IDStr"], str):
                raise ValueError("invalid module identity")
            clean.append(
                {
                    "instance_index": index,
                    "base_offset": offset,
                    "values": {
                        key: values[key] for key in ("ID", "IDStr") if key in values
                    },
                }
            )
        result["repeating"][name] = clean
    return result
