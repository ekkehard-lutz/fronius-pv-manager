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
