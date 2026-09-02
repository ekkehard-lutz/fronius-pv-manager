# Changelog

All notable changes to this project will be documented in this file. Future
releases will use semantic versioning.

## [Unreleased]

### Added

- Initial Fronius PV Manager project structure.
- MIT license and Home Assistant/HACS metadata.
- English and German localization foundation.
- Immutable SunSpec register and model core data types.
- Capability-based device metadata.
- Validation for register definitions, model layouts, and discovered models.
- Unit tests for the core model.
- CI-ready validation with Ruff and pytest.

## [v0.2.0-beta.9] - 2026-09-02

### Added

- Completed the writable-register inventory for SunSpec Models 123 and 124:
  18 writable Model 123 registers and 7 writable Model 124 registers.
- The packaged default write policy now lists all 25 writable registers
  explicitly. Only `MinRsvPct` and `ChaGriSet` remain enabled by default.
- Added NUMBER and SELECT controls for safely representable GEN24 registers.
  Low-level controls remain disabled by default in the Home Assistant Entity
  Registry.
- Represented `StorCtl_Mod` as one SELECT containing its documented bit
  combinations.

### Changed

- `WMaxLimPct_RmpTms` retains general read-write register metadata but is
  exposed as a read-only SENSOR on GEN24 because GEN24 treats it as read-only.
- `MinRsvPct` uses the project-authoritative hard range of 0 through 100%.

### Safety

- `VAChaMax` remains defined but unexposed because it is currently unsupported
  by GEN24 and has no authoritative finite semantic range.
- `OutPFSet` remains unexposed because its valid domain consists of two dynamic,
  nameplate-dependent intervals.
- Existing write safety is unchanged: explicit policy permission, semantic
  validation, exactly one physical write, verified readback, and
  non-optimistic state remain required.

### Validation

- Ruff passed with all checks successful.
- 566 tests passed with 1 warning.
- `git diff --check` passed.

## [v0.2.0-beta.8] - 2026-09-02

### Changed

- Added one shared persistent Modbus TCP endpoint and client per configured
  host and port. Bound device-ID views share that endpoint.
- Runtime polling and writes remain serialized through the shared endpoint.
- Failed requests reset the endpoint so later requests can reconnect.
- Multi-device partial recovery remains supported.

### Safety

- Uncertain writes are never retried automatically.
- Verified readback remains required after writes.

## [v0.2.0-beta.7] - 2026-09-01

### Changed

- Fresh Home Assistant entities now use deterministic, language-independent
  semantic object IDs. Normal sensor object IDs no longer derive from translated
  display names, while Home Assistant continues to own the device-name prefix.
- Fresh sensor IDs therefore include examples such as
  `sensor.speicher_discharging_current`,
  `sensor.speicher_state_of_charge`, and
  `sensor.smart_meter_ts_65a_3_exported_energy`.
- Model 160 uses runtime-classified semantic IDs such as `mppt_1_dc_power`,
  `mppt_2_dc_power`, `charging_power`, and `discharging_power`.
- Low-level writable controls retain the explicit `_reg_` convention, including
  `storage_reg_minimum_storage_reserve` and `storage_reg_grid_charging`.

### Compatibility

- Unique-ID construction is unchanged.
- Existing entity IDs from earlier beta releases are not migrated. A clean
  installation receives the new IDs automatically.
- No register semantics, Modbus transport, codec, write policy, or write-runtime
  behavior changed.

### Validation

- Ruff passed.
- 555 tests passed.
- `git diff --check` passed.

## [v0.2.0-beta.6] - 2026-09-01

### Changed

- Low-level writable register entities are now exposed independently of
  `write_policy.yaml` when authoritative metadata allows them to be represented
  safely.
- Low-level NUMBER and SELECT entities remain disabled by default in the Home
  Assistant Entity Registry. Enabling an entity does not grant Modbus write
  permission.
- Missing policy entries leave supported controls readable but write-protected.
- Added optional `enabled: false` policy entries to temporarily disable writing
  while retaining and validating configured constraints. Omitting `enabled`
  remains equivalent to `enabled: true` for compatibility.
- Policy changes continue to require an integration reload or Home Assistant
  restart.
- NUMBER controls without finite authoritative hard bounds remain unexposed;
  no limits are guessed or invented.
- Without an active narrowing policy, NUMBER presentation uses authoritative
  hard limits and SELECT presentation uses all documented enum values.
- Localized low-level control names now use a `Register` prefix.
- Fresh registrations use stable language-independent low-level object IDs,
  including `number.storage_reg_minimum_storage_reserve` and
  `select.storage_reg_grid_charging`.

### Safety

- Missing or disabled write approval is rejected before Modbus I/O.
- `WriteRuntime` remains the authoritative write-permission boundary.
- Existing exactly-one-write, verified-readback, non-optimistic behavior is
  preserved.
- Policy may only narrow authoritative register constraints, and invalid policy
  snapshots remain fail-closed.

### Compatibility

- Existing `write_policy.yaml` files without `enabled` remain valid.
- The packaged default write policy remains unchanged.
- No registry migration is included for previous beta entity IDs because beta
  test installations are expected to be removed before fresh-install testing
  of the new naming scheme.
