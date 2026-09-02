"""Audit authoritative constraints for writable SunSpec register definitions."""

import pytest

from custom_components.fronius_pv_manager.codec import encode_register_value
from custom_components.fronius_pv_manager.models import (
    EntityPlatform,
    RegisterAccess,
    ValueRange,
)
from custom_components.fronius_pv_manager.register_maps import (
    MODEL_120,
    MODEL_121,
    MODEL_122,
    MODEL_123,
    MODEL_124,
)

MODELS = (MODEL_120, MODEL_121, MODEL_122, MODEL_123, MODEL_124)


def writable_registers():
    """Return writable definitions keyed by stable semantic coordinates."""
    return {
        (model.model_ids[0], register.name): register
        for model in MODELS
        for register in model.registers
        if register.access is RegisterAccess.READ_WRITE
    }


def test_complete_writable_register_inventory() -> None:
    """Models 120 through 124 expose exactly the audited writable registers."""
    assert set(writable_registers()) == {
        (123, "Conn_WinTms"),
        (123, "Conn_RvrtTms"),
        (123, "Conn"),
        (123, "WMaxLimPct"),
        (123, "WMaxLimPct_WinTms"),
        (123, "WMaxLimPct_RvrtTms"),
        (123, "WMaxLimPct_RmpTms"),
        (123, "WMaxLim_Ena"),
        (123, "OutPFSet"),
        (123, "OutPFSet_WinTms"),
        (123, "OutPFSet_RvrtTms"),
        (123, "OutPFSet_RmpTms"),
        (123, "OutPFSet_Ena"),
        (123, "VArMaxPct"),
        (123, "VArPct_WinTms"),
        (123, "VArPct_RvrtTms"),
        (123, "VArPct_RmpTms"),
        (123, "VArPct_Ena"),
        (124, "StorCtl_Mod"),
        (124, "VAChaMax"),
        (124, "MinRsvPct"),
        (124, "OutWRte"),
        (124, "InWRte"),
        (124, "InOutWRte_RvrtTms"),
        (124, "ChaGriSet"),
    }


def test_all_documented_writable_numeric_ranges_are_authoritative() -> None:
    """Every worksheet-established writable range is encoded in the model."""
    registers = writable_registers()
    ranges = {
        coordinate: register.valid_range
        for coordinate, register in registers.items()
        if register.valid_range is not None
    }
    assert ranges == {
        (123, "Conn_WinTms"): ValueRange(0, 300),
        (123, "Conn_RvrtTms"): ValueRange(0, 28800),
        (123, "WMaxLimPct"): ValueRange(0, 100),
        (123, "WMaxLimPct_WinTms"): ValueRange(0, 300),
        (123, "WMaxLimPct_RvrtTms"): ValueRange(0, 28800),
        (123, "WMaxLimPct_RmpTms"): ValueRange(0, 65534),
        (123, "OutPFSet_WinTms"): ValueRange(0, 300),
        (123, "OutPFSet_RvrtTms"): ValueRange(0, 28800),
        (123, "OutPFSet_RmpTms"): ValueRange(0, 65534),
        (123, "VArMaxPct"): ValueRange(-100, 100),
        (123, "VArPct_WinTms"): ValueRange(0, 300),
        (123, "VArPct_RvrtTms"): ValueRange(0, 28800),
        (123, "VArPct_RmpTms"): ValueRange(0, 65534),
        (124, "MinRsvPct"): ValueRange(0, 100),
        (124, "OutWRte"): ValueRange(-100, 100),
        (124, "InWRte"): ValueRange(-100, 100),
        (124, "InOutWRte_RvrtTms"): ValueRange(0, 28800),
    }
    assert all(value.step is None for value in ranges.values())


def test_minimum_reserve_hard_range_is_enforced_before_scaling() -> None:
    """The project-authoritative percentage range is enforced before scaling."""
    definition = writable_registers()[(124, "MinRsvPct")]
    assert encode_register_value(definition, 0, -2) == (0,)
    assert encode_register_value(definition, 100, -2) == (10000,)
    for value in (-1, 101):
        with pytest.raises(ValueError, match="valid range"):
            encode_register_value(definition, value, -2)


def test_all_documented_writable_enum_and_bitfield_constraints() -> None:
    """Writable discrete controls retain exact documented values and masks."""
    registers = writable_registers()
    assert {
        coordinate: dict(register.enum)
        for coordinate, register in registers.items()
        if register.enum is not None
    } == {
        (123, "Conn"): {0: "Disconnected", 1: "Connected"},
        (123, "WMaxLim_Ena"): {0: "Disabled", 1: "Enabled"},
        (123, "OutPFSet_Ena"): {0: "Disabled", 1: "Enabled"},
        (123, "VArPct_Ena"): {0: "Disabled", 1: "Enabled"},
        (124, "ChaGriSet"): {
            0: "PV (Charging from grid disabled)",
            1: "GRID (Charging from grid enabled)",
        },
    }
    assert {
        coordinate: dict(register.bitfield)
        for coordinate, register in registers.items()
        if register.bitfield is not None
    } == {
        (124, "StorCtl_Mod"): {0x0001: "CHARGE", 0x0002: "DISCHARGE"},
    }


def test_writable_numeric_registers_without_authoritative_semantic_ranges() -> None:
    """Unverified semantic limits remain explicit omissions for manual review."""
    registers = writable_registers()
    unconstrained = {
        coordinate
        for coordinate, register in registers.items()
        if register.valid_range is None
        and register.enum is None
        and register.bitfield is None
    }
    assert unconstrained == {
        (123, "OutPFSet"),
        (124, "VAChaMax"),
    }


def test_writable_control_catalog_is_complete_and_safely_representable() -> None:
    """GEN24 exposure excludes exactly three RW registers for distinct reasons."""
    registers = writable_registers()
    read_only_ha_representation = {
        coordinate
        for coordinate, register in registers.items()
        if register.entity is not None
        and register.entity.platform is EntityPlatform.SENSOR
    }
    unsafe_number_domain = {
        coordinate
        for coordinate, register in registers.items()
        if register.entity is not None
        and register.entity.platform is EntityPlatform.NUMBER
        and (
            register.valid_range is None
            or register.valid_range.minimum is None
            or register.valid_range.maximum is None
        )
    }
    unsupported = {
        coordinate
        for coordinate, register in registers.items()
        if register.entity is None
    }
    assert read_only_ha_representation == {(123, "WMaxLimPct_RmpTms")}
    assert unsafe_number_domain == {(123, "OutPFSet")}
    assert unsupported == {(124, "VAChaMax")}
    assert len(
        registers
    ) - len(read_only_ha_representation | unsafe_number_domain | unsupported) == 22
    assert registers[(123, "OutPFSet_RmpTms")].entity.platform is EntityPlatform.NUMBER
    assert (
        registers[(124, "InOutWRte_RvrtTms")].entity.platform
        is EntityPlatform.NUMBER
    )
    reserve = registers[(124, "MinRsvPct")]
    assert reserve.entity.platform is EntityPlatform.NUMBER
    assert reserve.valid_range == ValueRange(0, 100)
    assert all(
        register.access is RegisterAccess.READ_WRITE
        for model in MODELS
        for register in model.registers
        if register.entity is not None
        and register.entity.platform in {EntityPlatform.NUMBER, EntityPlatform.SELECT}
    )
