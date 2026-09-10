"""JSON-safe entity topology, containing identity metadata but no measurements."""

from dataclasses import asdict

from .model_decoder import DecodedModel, DecodedRepeatingBlockInstance
from .models import DiscoveredModel, RegisterValue

CONF_TOPOLOGY = "topology"


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
