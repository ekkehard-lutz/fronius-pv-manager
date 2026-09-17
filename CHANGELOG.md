# Changelog

All notable changes to Fronius PV Manager are documented here, starting with
v1.0.0, the first official stable release. Pre-1.0 development releases remain
available in Git history and GitHub releases.

## [Unreleased]

## [v1.1.0] - 2026-09-17

### Added

- Nine semantic sensors: PV Power, Grid Import Power, Grid Export Power,
  Consumption Power, Autarky, Self Consumption, Inverter Efficiency,
  Rectifier Efficiency, and Battery Lifetime Efficiency.
- Separate conversion efficiencies for DC-to-AC and AC-to-DC operation;
  incompatible conversion directions remain unavailable. Battery Lifetime
  Efficiency estimates cumulative round-trip efficiency including stored energy.

### Reliability

- Self Consumption reports 100% with zero or negative inverter AC power and
  no grid export, including forced grid charging. Positive-AC behavior is preserved.
- Defensive numeric validation makes malformed values and calculation overflow
  unavailable instead of exposing invalid readings or errors.
- Ambiguous Model 160 PV and Model 203 meter sources are rejected; missing
  individual MPPT readings still allow partial PV totals within one valid source.
- Regression coverage verifies stable sensor identities through persisted-topology
  offline startup, independent device outages, and recovery in both conversion directions.
- The v1.1.0 functionality has been successfully runtime-tested on a real
  Fronius GEN24 installation.

## [v1.0.1] - 2026-09-16

### Changed

- Start public release history at v1.0.0 and consolidate its shipped features
  and release-readiness fixes into the initial stable release summary.
- Remove pre-1.0 development release sections from this changelog and refresh
  outdated README version wording while preserving technical and safety guidance.
- Update integration version metadata to v1.0.1. No runtime, API, entity,
  register, or Write Policy behavior changes.

## [v1.0.0] - 2026-09-16

Initial official stable release of Fronius PV Manager.

### Added

- Local SunSpec Modbus TCP discovery and polling through a shared persistent
  endpoint, supporting multiple configured device IDs and integer + scale-factor
  models 1, 103, 120–124, 160, and 203.
- Capability-based inverter, storage, and Smart Meter devices with operational
  and diagnostic sensors, classified Model 160 channels, stable entity identities,
  and English/German localization.
- Offline startup using cached logical topology, per-device availability and
  automatic recovery, including later-discovered capabilities.
- Expert low-level Model 123/124 controls, disabled by default in the Entity
  Registry, governed by a fail-closed installation Write Policy and authoritative
  register validation. Entity enablement does not grant write permission.
- Seven high-level storage controls: minimum reserve, grid-charging permission,
  minimum/maximum charge power, minimum/maximum discharge power, and operating
  mode; plus a read-only Storage control status sensor.
- Persistent semantic whole-watt profiles, fixed 1 W UI steps, WChaMax-based
  conversion, inward quantization, and rejection of unrepresentable power windows.
  Explicit neutral Automatic does not require WChaMax or create a watt profile.
- HLC/LLC coexistence with observed overrides: last explicit write wins, no
  background target reassertion, and no lock or ownership over external clients.
- Programmatic Energy Manager API for complete power windows and remote control,
  with per-device ownership, heartbeat/watchdog leases, and runtime-only
  pre-remote reserve, grid-permission, and user-profile snapshots.
- Neutral-Automatic-first release and expiry cleanup, verified policy/profile
  restoration, and retained retry state after incomplete cleanup. Previous
  manual limits are not automatically restored.
- Optional local, read-only Solar API Battery operation mode, Backup mode, and
  Battery standby entities with independent availability, automatic recovery,
  and storage-capability gating. SunSpec remains the primary interface.

### Safety and reliability

- Live topology validation separate from the offline entity cache, with bounded
  rediscovery and session/discovery-bound write plans that reject stale addresses.
  Invalidation during cleanup preparation aborts stale plans without rebuilding
  or executing them; recovery allows a fresh explicit cleanup retry.
- Cancellation-safe shared I/O ownership for polling, discovery, writes and close,
  including repeated cancellation. Each register write is attempted at most once and
  verified by readback; HLC actions use ordered multi-register sequences without
  speculative rollback or automatic retries of uncertain writes.
- HA Store revision/readback confirmation that detects suppressed persistence
  failures and retains cleanup retry state, without claiming power-loss durability.
- Defensive persisted-data validation and malformed-policy handling that preserve
  readable setup while rejecting unsafe writes.
- Idempotent Home Assistant stop/unload cleanup that cancels watchdogs, drains
  active I/O and closes endpoints without automatic remote release/restore writes.
  Startup never replays persisted control settings.

### Validation

- 954 automated tests passed on Home Assistant 2026.9.1 and the supported minimum
  2026.8.0, including real-worker cancellation races, topology relocation,
  persistence failures, shutdown and malformed-input regressions.
- Hardware validation reported on a Fronius Symo GEN24 10.0, BYD Battery-Box
  Premium HVM 11.0 and Fronius Smart Meter TS 65A-3, including storage control and
  remote acquire, heartbeat, release and watchdog cleanup. These observations
  apply to the tested setup, not all firmware, batteries or Full Backup wiring.
