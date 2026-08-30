"""Tests for generic complete SunSpec model payload decoding."""

from dataclasses import FrozenInstanceError

import pytest

from custom_components.fronius_pv_manager.model_decoder import decode_model
from custom_components.fronius_pv_manager.models import (
    RegisterAccess,
    RegisterDataType,
    RegisterDefinition,
    SunSpecModelDefinition,
)
from custom_components.fronius_pv_manager.register_maps import MODEL_160

_MODULES = (
    (1, [0x4D50, 0x5054, 0x2031, 0, 0, 0, 0, 0]),
    (2, [0x4D50, 0x5054, 0x2032, 0, 0, 0, 0, 0]),
    (3, [0x5374, 0x4368, 0x6120, 0x3300, 0, 0, 0, 0]),
    (4, [0x5374, 0x4469, 0x7343, 0x6861, 0x2034, 0, 0, 0]),
)


def model_160_payload() -> list[int]:
    """Build an 88-word payload from observed identity and fixed values."""
    payload = [0] * 88
    payload[:8] = [
        0xFFFD,
        0xFFFE,
        0xFFFF,
        0xFFFE,
        0xFFFF,
        0xFFFF,
        0x0004,
        0xFFFF,
    ]
    for index, (module_id, identity) in enumerate(_MODULES):
        base = 8 + index * 20
        payload[base] = module_id
        payload[base + 1 : base + 9] = identity
        payload[base + 9] = 1234
        payload[base + 10] = 5123
        payload[base + 11] = 5000
        payload[base + 12 : base + 14] = [0, 12345]
    return payload


def register(
    name: str,
    offset: int,
    data_type: RegisterDataType,
    *,
    size: int = 1,
    scale_factor: str | None = None,
) -> RegisterDefinition:
    """Create a compact fixed register for structural decoder tests."""
    return RegisterDefinition(
        name=name,
        offset=offset,
        size=size,
        data_type=data_type,
        access=RegisterAccess.READ_ONLY,
        scale_factor=scale_factor,
    )


def test_decodes_model_160_fixed_values_and_instances() -> None:
    """A complete Model 160 payload yields fixed values and four modules."""
    decoded = decode_model(MODEL_160, model_160_payload())

    assert decoded.fixed["DCA_SF"].value == -3
    assert decoded.fixed["DCV_SF"].value == -2
    assert decoded.fixed["DCW_SF"].value == -1
    assert decoded.fixed["DCWH_SF"].value == -2
    assert decoded.fixed["N"].value == 4
    instances = decoded.repeating["module"]
    assert len(instances) == 4
    assert [instance.base_offset for instance in instances] == [8, 28, 48, 68]
    assert [instance.instance_index for instance in instances] == [0, 1, 2, 3]
    assert [instance.values["ID"].value for instance in instances] == [1, 2, 3, 4]
    assert [instance.values["IDStr"].value for instance in instances] == [
        "MPPT 1",
        "MPPT 2",
        "StCha 3",
        "StDisCha 4",
    ]


def test_model_160_automatically_resolves_measurement_scale_factors() -> None:
    """Repeating measurements use named decoded fixed scale factors."""
    first = decode_model(MODEL_160, model_160_payload()).repeating["module"][0]

    assert first.values["DCA"].value == 1.234
    assert first.values["DCV"].value == 51.23
    assert first.values["DCW"].value == 500
    assert first.values["DCWH"].value == 123.45


def test_decoded_result_mappings_are_immutable() -> None:
    """Frozen results do not expose mutable fixed, repeating, or value mappings."""
    decoded = decode_model(MODEL_160, model_160_payload())
    instance = decoded.repeating["module"][0]

    with pytest.raises(TypeError):
        decoded.fixed["N"] = decoded.fixed["N"]  # type: ignore[index]
    with pytest.raises(TypeError):
        decoded.repeating["other"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        instance.values["ID"] = instance.values["ID"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        instance.base_offset = 0  # type: ignore[misc]


def test_payload_too_short_for_fixed_register_is_rejected() -> None:
    """A fixed register slice may not be silently truncated."""
    with pytest.raises(ValueError, match="too short"):
        decode_model(MODEL_160, [0] * 7)


def test_incomplete_trailing_repeating_block_is_rejected() -> None:
    """Trailing words that cannot form a complete instance are structural errors."""
    with pytest.raises(ValueError, match="trailing registers"):
        decode_model(MODEL_160, model_160_payload()[:-1])


def test_missing_scale_factor_definition_is_rejected() -> None:
    """A named scale factor must identify a fixed model register."""
    definition = SunSpecModelDefinition(
        (999,),
        "missing scale factor",
        (register("value", 0, RegisterDataType.UINT16, scale_factor="missing"),),
    )

    with pytest.raises(ValueError, match="not a fixed register"):
        decode_model(definition, [1])


def test_non_numeric_scale_factor_source_is_rejected() -> None:
    """A scale-factor source must decode to an integer."""
    definition = SunSpecModelDefinition(
        (999,),
        "string scale factor",
        (
            register("sf", 0, RegisterDataType.STRING),
            register("value", 1, RegisterDataType.UINT16, scale_factor="sf"),
        ),
    )

    with pytest.raises(ValueError, match="must decode to an integer"):
        decode_model(definition, [0x3100, 10])


def test_invalid_scale_factor_makes_dependent_value_none() -> None:
    """An invalid scale factor propagates None while preserving dependent raw."""
    definition = SunSpecModelDefinition(
        (999,),
        "invalid scale factor",
        (
            register("sf", 0, RegisterDataType.SUNSSF),
            register("value", 1, RegisterDataType.UINT16, scale_factor="sf"),
        ),
    )

    decoded = decode_model(definition, [0x8000, 123])

    assert decoded.fixed["sf"].value is None
    assert decoded.fixed["value"].raw == 123
    assert decoded.fixed["value"].value is None


def test_invalid_dependent_sentinel_remains_none() -> None:
    """A dependent register's own invalid sentinel remains invalid after scaling."""
    definition = SunSpecModelDefinition(
        (999,),
        "invalid dependent",
        (
            register("sf", 0, RegisterDataType.SUNSSF),
            register("value", 1, RegisterDataType.UINT16, scale_factor="sf"),
        ),
    )

    decoded = decode_model(definition, [0, 0xFFFF])

    assert decoded.fixed["value"].raw == 0xFFFF
    assert decoded.fixed["value"].value is None


@pytest.mark.parametrize("word", [-1, 0x10000, "1"])
def test_malformed_payload_word_is_rejected(word: object) -> None:
    """Every payload element must be an unsigned 16-bit integer."""
    payload: list[object] = model_160_payload()
    payload[0] = word

    with pytest.raises(ValueError, match="0 through 65535"):
        decode_model(MODEL_160, payload)  # type: ignore[arg-type]
