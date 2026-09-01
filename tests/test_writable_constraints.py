"""Audit authoritative constraints for writable SunSpec register definitions."""

import pytest

from custom_components.fronius_pv_manager.codec import encode_register_value
from custom_components.fronius_pv_manager.models import RegisterAccess, ValueRange
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
        (123, "WMaxLimPct_WinTms"): ValueRange(0, 300),
        (123, "WMaxLimPct_RvrtTms"): ValueRange(0, 28800),
        (123, "OutPFSet_WinTms"): ValueRange(0, 300),
        (123, "OutPFSet_RvrtTms"): ValueRange(0, 28800),
        (123, "VArPct_WinTms"): ValueRange(0, 300),
        (123, "VArPct_RvrtTms"): ValueRange(0, 28800),
        (124, "MinRsvPct"): ValueRange(0, 100),
        (124, "OutWRte"): ValueRange(-100, 100),
        (124, "InWRte"): ValueRange(-100, 100),
        (124, "InOutWRte_RvrtTms"): ValueRange(0, 28800),
    }
    assert all(value.step is None for value in ranges.values())


def test_minimum_reserve_hard_range_is_enforced_before_scaling() -> None:
    """The core encoder rejects MinRsvPct semantics outside zero to one hundred."""
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
        (123, "Conn"): {0x0001: "Connected"},
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
        (123, "Conn_WinTms"),
        (123, "Conn_RvrtTms"),
        (123, "WMaxLimPct"),
        (123, "OutPFSet"),
        (123, "OutPFSet_RmpTms"),
        (123, "VArMaxPct"),
        (123, "VArPct_RmpTms"),
        (124, "VAChaMax"),
    }
