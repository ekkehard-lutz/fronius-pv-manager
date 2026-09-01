"""Internal helpers for attaching entity catalog metadata to register maps."""

from collections.abc import Mapping
from dataclasses import replace

from ..models import (
    EntityCategoryHint,
    EntityDefinition,
    EntityPlatform,
    PhysicalDeviceRole,
    SunSpecModelDefinition,
)

_SUGGESTED_OBJECT_IDS: dict[int, dict[str, str]] = {
    103: {
        "A": "ac_current",
        "AphA": "phase_a_current",
        "AphB": "phase_b_current",
        "AphC": "phase_c_current",
        "PPVphAB": "ac_voltage_l1_l2",
        "PPVphBC": "ac_voltage_l2_l3",
        "PPVphCA": "ac_voltage_l3_l1",
        "PhVphA": "ac_voltage_l1_n",
        "PhVphB": "ac_voltage_l2_n",
        "PhVphC": "ac_voltage_l3_n",
        "W": "ac_power",
        "Hz": "ac_frequency",
        "VA": "apparent_power",
        "VAr": "reactive_power",
        "PF": "power_factor",
        "WH": "ac_energy",
        "DCA": "dc_current",
        "DCV": "dc_voltage",
        "DCW": "dc_power",
        "TmpCab": "cabinet_temperature",
        "TmpSnk": "heat_sink_temperature",
        "TmpTrns": "transformer_temperature",
        "TmpOt": "other_temperature",
        "St": "operating_state",
        "StVnd": "vendor_operating_state",
        "Evt1": "sunspec_event_flags",
        "Evt2": "reserved_event_flags",
        "EvtVnd1": "customer_event_severity_flags",
        "EvtVnd2": "technician_event_severity_flags",
        "EvtVnd3": "vendor_event_flags_1",
        "EvtVnd4": "vendor_event_flags_2",
    },
    120: {
        "DERTyp": "distributed_energy_resource_type",
        "WRtg": "continuous_power_rating",
        "VARtg": "continuous_apparent_power_rating",
        "VArRtgQ1": "reactive_power_rating_quadrant_1",
        "VArRtgQ2": "reactive_power_rating_quadrant_2",
        "VArRtgQ3": "reactive_power_rating_quadrant_3",
        "VArRtgQ4": "reactive_power_rating_quadrant_4",
        "ARtg": "maximum_ac_current_rating",
        "PFRtgQ1": "rated_minimum_power_factor_quadrant_1",
        "PFRtgQ2": "rated_minimum_power_factor_quadrant_2",
        "PFRtgQ3": "rated_minimum_power_factor_quadrant_3",
        "PFRtgQ4": "rated_minimum_power_factor_quadrant_4",
        "WHRtg": "nominal_storage_energy_rating",
        "AhrRtg": "usable_battery_capacity",
        "MaxChaRte": "maximum_storage_charge_rate",
        "MaxDisChaRte": "maximum_storage_discharge_rate",
    },
    121: {
        "WMax": "maximum_power_output_setting",
        "VRef": "reference_voltage",
        "VRefOfs": "reference_voltage_offset",
        "VMax": "maximum_voltage_setpoint",
        "VMin": "minimum_voltage_setpoint",
        "VAMax": "maximum_apparent_power_setpoint",
        "VArMaxQ1": "maximum_reactive_power_quadrant_1",
        "VArMaxQ2": "maximum_reactive_power_quadrant_2",
        "VArMaxQ3": "maximum_reactive_power_quadrant_3",
        "VArMaxQ4": "maximum_reactive_power_quadrant_4",
        "WGra": "active_power_ramp_rate",
        "PFMinQ1": "minimum_power_factor_quadrant_1",
        "PFMinQ2": "minimum_power_factor_quadrant_2",
        "PFMinQ3": "minimum_power_factor_quadrant_3",
        "PFMinQ4": "minimum_power_factor_quadrant_4",
        "VArAct": "reactive_power_action",
        "ClcTotVA": "total_apparent_power_calculation",
        "MaxRmpRte": "maximum_ramp_rate",
        "ECPNomHz": "nominal_ecp_frequency",
        "ConnPh": "connected_phase",
    },
    122: {
        "PVConn": "pv_connection_status",
        "StorConn": "storage_connection_status",
        "ECPConn": "ecp_connection_status",
        "ActWh": "active_energy_output",
        "ActVAh": "apparent_energy_output",
        "ActVArhQ1": "reactive_energy_quadrant_1",
        "ActVArhQ2": "reactive_energy_quadrant_2",
        "ActVArhQ3": "reactive_energy_quadrant_3",
        "ActVArhQ4": "reactive_energy_quadrant_4",
        "VArAval": "available_reactive_power",
        "WAval": "available_active_power",
        "StSetLimMsk": "setpoint_limit_status",
        "StActCtl": "active_controls",
        "TmSrc": "time_source",
        "Tms": "timestamp",
        "RtSt": "ride_through_status",
        "Ris": "isolation_resistance",
    },
    123: {
        "Conn_WinTms": "inverter_reg_connection_time_window",
        "Conn_RvrtTms": "inverter_reg_connection_reversion_timeout",
        "WMaxLimPct": "inverter_reg_maximum_power_output",
        "WMaxLimPct_WinTms": "inverter_reg_power_limit_time_window",
        "WMaxLimPct_RvrtTms": "inverter_reg_power_limit_reversion_timeout",
        "WMaxLimPct_RmpTms": "power_limit_ramp_time",
        "OutPFSet_WinTms": "inverter_reg_power_factor_time_window",
        "OutPFSet": "inverter_reg_power_factor_setting",
        "OutPFSet_RvrtTms": "inverter_reg_power_factor_reversion_timeout",
        "OutPFSet_RmpTms": "inverter_reg_power_factor_ramp_time",
        "VArWMaxPct": "reactive_power_percentage_of_maximum_power",
        "VArAvalPct": "reactive_power_percentage_of_available_power",
        "VArMaxPct": "inverter_reg_reactive_power_limit",
        "VArPct_WinTms": "inverter_reg_reactive_power_time_window",
        "VArPct_RvrtTms": "inverter_reg_reactive_power_reversion_timeout",
        "VArPct_RmpTms": "inverter_reg_reactive_power_ramp_time",
        "VArPct_Mod": "reactive_power_limit_mode",
    },
    124: {
        "WChaMax": "maximum_charging_power",
        "WChaGra": "maximum_charging_ramp_rate",
        "WDisChaGra": "maximum_discharging_ramp_rate",
        "VAChaMax": "storage_reg_maximum_charging_apparent_power",
        "MinRsvPct": "storage_reg_minimum_storage_reserve",
        "ChaState": "state_of_charge",
        "StorAval": "available_capacity_above_reserve",
        "InBatV": "internal_battery_voltage",
        "ChaSt": "charge_status",
        "OutWRte": "storage_reg_maximum_discharge_rate",
        "InWRte": "storage_reg_maximum_charge_rate",
        "InOutWRte_RvrtTms": "storage_reg_charge_discharge_reversion_timeout",
        "ChaGriSet": "storage_reg_grid_charging",
    },
    203: {
        "A": "ac_current",
        "AphA": "phase_a_current",
        "AphB": "phase_b_current",
        "AphC": "phase_c_current",
        "PhV": "average_ac_voltage_l_n",
        "PhVphA": "ac_voltage_l1_n",
        "PhVphB": "ac_voltage_l2_n",
        "PhVphC": "ac_voltage_l3_n",
        "PPV": "average_ac_voltage_l_l",
        "PhVphAB": "ac_voltage_l1_l2",
        "PhVphBC": "ac_voltage_l2_l3",
        "PhVphCA": "ac_voltage_l3_l1",
        "Hz": "ac_frequency",
        "W": "ac_power",
        "WphA": "phase_a_ac_power",
        "WphB": "phase_b_ac_power",
        "WphC": "phase_c_ac_power",
        "VA": "apparent_power",
        "VAphA": "phase_a_apparent_power",
        "VAphB": "phase_b_apparent_power",
        "VAphC": "phase_c_apparent_power",
        "VAR": "reactive_power",
        "VARphA": "phase_a_reactive_power",
        "VARphB": "phase_b_reactive_power",
        "VARphC": "phase_c_reactive_power",
        "PF": "power_factor",
        "PFphA": "phase_a_power_factor",
        "PFphB": "phase_b_power_factor",
        "PFphC": "phase_c_power_factor",
        "TotWhExp": "exported_energy",
        "TotWhExpPhA": "phase_a_exported_energy",
        "TotWhExpPhB": "phase_b_exported_energy",
        "TotWhExpPhC": "phase_c_exported_energy",
        "TotWhImp": "imported_energy",
        "TotWhImpPhA": "phase_a_imported_energy",
        "TotWhImpPhB": "phase_b_imported_energy",
        "TotWhImpPhC": "phase_c_imported_energy",
        "TotVAhExp": "exported_apparent_energy",
        "TotVAhExpPhA": "phase_a_exported_apparent_energy",
        "TotVAhExpPhB": "phase_b_exported_apparent_energy",
        "TotVAhExpPhC": "phase_c_exported_apparent_energy",
        "TotVAhImp": "imported_apparent_energy",
        "TotVAhImpPhA": "phase_a_imported_apparent_energy",
        "TotVAhImpPhB": "phase_b_imported_apparent_energy",
        "TotVAhImpPhC": "phase_c_imported_apparent_energy",
        "TotVArhImpQ1": "imported_reactive_energy_quadrant_1",
        "TotVArhImpQ1PhA": "phase_a_imported_reactive_energy_quadrant_1",
        "TotVArhImpQ1PhB": "phase_b_imported_reactive_energy_quadrant_1",
        "TotVArhImpQ1PhC": "phase_c_imported_reactive_energy_quadrant_1",
        "TotVArhImpQ2": "imported_reactive_energy_quadrant_2",
        "TotVArhImpQ2PhA": "phase_a_imported_reactive_energy_quadrant_2",
        "TotVArhImpQ2PhB": "phase_b_imported_reactive_energy_quadrant_2",
        "TotVArhImpQ2PhC": "phase_c_imported_reactive_energy_quadrant_2",
        "TotVArhExpQ3": "exported_reactive_energy_quadrant_3",
        "TotVArhExpQ3PhA": "phase_a_exported_reactive_energy_quadrant_3",
        "TotVArhExpQ3PhB": "phase_b_exported_reactive_energy_quadrant_3",
        "TotVArhExpQ3PhC": "phase_c_exported_reactive_energy_quadrant_3",
        "TotVArhExpQ4": "exported_reactive_energy_quadrant_4",
        "TotVArhExpQ4PhA": "phase_a_exported_reactive_energy_quadrant_4",
        "TotVArhExpQ4PhB": "phase_b_exported_reactive_energy_quadrant_4",
        "TotVArhExpQ4PhC": "phase_c_exported_reactive_energy_quadrant_4",
        "Evt": "meter_event_flags",
    },
}


def entity(
    model_id: int,
    register_name: str,
    *,
    platform: EntityPlatform = EntityPlatform.SENSOR,
    category: EntityCategoryHint = EntityCategoryHint.PRIMARY,
    enabled: bool = True,
    role: PhysicalDeviceRole | None = None,
    block_name: str | None = None,
    translate_enum_values: bool = False,
    device_class: str | None = None,
    state_class: str | None = None,
    presentation_unit: str | None = None,
    suggested_object_id: str | None = None,
) -> EntityDefinition:
    """Create stable Home Assistant-independent entity metadata."""
    block = f"_{block_name}" if block_name is not None else ""
    key = f"model_{model_id}{block}_{register_name.lower()}"
    return EntityDefinition(
        platform=platform,
        key=key,
        translation_key=key,
        translate_enum_values=translate_enum_values,
        device_class=device_class,
        state_class=state_class,
        presentation_unit=presentation_unit,
        category=category,
        enabled_by_default=enabled,
        device_role=role,
        suggested_object_id=(
            suggested_object_id
            if suggested_object_id is not None
            else _SUGGESTED_OBJECT_IDS.get(model_id, {}).get(register_name)
        ),
    )


def attach_entities(
    definition: SunSpecModelDefinition,
    fixed: dict[str, EntityDefinition],
    *,
    repeating: dict[str, dict[str, EntityDefinition]] | None = None,
) -> SunSpecModelDefinition:
    """Return a definition with catalog entries attached by register name."""
    fixed_names = {register.name for register in definition.registers}
    if unknown := set(fixed) - fixed_names:
        raise ValueError(f"unknown fixed entity registers: {sorted(unknown)}")
    registers = tuple(
        replace(register, entity=fixed.get(register.name))
        for register in definition.registers
    )
    repeating = repeating or {}
    block_names = {block.name for block in definition.repeating_blocks}
    if unknown_blocks := set(repeating) - block_names:
        raise ValueError(f"unknown repeating entity blocks: {sorted(unknown_blocks)}")
    blocks = []
    for block in definition.repeating_blocks:
        entities = repeating.get(block.name, {})
        register_names = {register.name for register in block.registers}
        if unknown := set(entities) - register_names:
            raise ValueError(
                f"unknown entity registers in block {block.name!r}: {sorted(unknown)}"
            )
        blocks.append(
            replace(
                block,
                registers=tuple(
                    replace(register, entity=entities.get(register.name))
                    for register in block.registers
                ),
            )
        )
    return replace(definition, registers=registers, repeating_blocks=tuple(blocks))


def attach_help_texts(
    definition: SunSpecModelDefinition,
    fixed: dict[str, Mapping[str, str]],
    *,
    repeating: dict[str, dict[str, Mapping[str, str]]] | None = None,
) -> SunSpecModelDefinition:
    """Return a definition with project-maintained explanatory text attached."""
    fixed_names = {register.name for register in definition.registers}
    if unknown := set(fixed) - fixed_names:
        raise ValueError(f"unknown fixed help registers: {sorted(unknown)}")
    registers = tuple(
        replace(register, help_text=fixed.get(register.name))
        for register in definition.registers
    )
    repeating = repeating or {}
    blocks = tuple(
        replace(
            block,
            registers=tuple(
                replace(
                    register,
                    help_text=repeating.get(block.name, {}).get(register.name),
                )
                for register in block.registers
            ),
        )
        for block in definition.repeating_blocks
    )
    return replace(definition, registers=registers, repeating_blocks=blocks)
