# Fronius PV Manager

Fronius PV Manager is a local-polling Home Assistant custom integration for
Fronius systems that expose SunSpec over Modbus TCP. It discovers supported
inverter, storage, and Smart Meter capabilities and organizes their entities as
separate Home Assistant devices.

Version 0.2.0 is the first stable release. It provides the stable low-level
communication, discovery, sensor, and guarded register-control foundation.
The development version adds high-level Home Assistant storage controls.

## For Home Assistant users

### Features

- Local SunSpec discovery and polling over Modbus TCP.
- Inverter, storage, and Smart Meter device roles based on discovered models.
- Multiple Modbus device IDs on one configured host and port.
- Stable, language-independent entity identities with translated display names.
- Operational and diagnostic sensors backed by reviewed register definitions.
- English and German Home Assistant translations.
- Device-specific availability: one unavailable Modbus device does not hide
  healthy devices on the same endpoint.

Normal users do not need to edit register maps or the write policy. Low-level
writable entities are intended for experts and are disabled in the Entity
Registry by default.

### Requirements and compatibility

- Home Assistant 2026.8.0 or newer, as declared in `hacs.json`.
- A Fronius device with SunSpec Modbus TCP enabled.
- Network access from Home Assistant to the configured host and TCP port.

Support is capability-based. Compatibility depends on the SunSpec models and
registers exposed by the device; universal support for every Fronius product is
not claimed.

### Tested hardware and compatibility

Fronius PV Manager v0.2.0 was developed and hardware-tested with:

- Fronius Symo GEN24 10.0
- BYD Battery-Box Premium HVM 11.0
- Fronius Smart Meter TS 65A-3

These exact devices are hardware-validated. The integration discovers
capabilities from SunSpec models, registers, and their semantics rather than
primarily matching product names. Other Fronius devices exposing the same
supported models with compatible register semantics are expected to work, but
remain unverified until tested on real hardware. This is not a blanket
compatibility claim for all GEN24 inverters, BYD batteries, or Fronius Smart
Meters.

### Installation with HACS

This repository does not claim inclusion in the default HACS store. Install it
as a custom repository:

1. In HACS, open **Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/ekkehard-lutz/fronius-pv-manager` with category
   **Integration**.
4. Find **Fronius PV Manager**, install it, and restart Home Assistant.

### Configuration

Before configuration, enable SunSpec Modbus TCP on the Fronius equipment using
the vendor documentation. Then in Home Assistant:

1. Open **Settings → Devices & services**.
2. Select **Add integration** and choose **Fronius PV Manager**.
3. Enter the device host, Modbus TCP port, and comma-separated Modbus device IDs.

The default port is `502`. Device IDs must be unique values from 1 through 247;
common examples are inverter ID `1` and meter ID `200`. The integration does not
perform DNS resolution while normalizing the configured host.

Discovery creates Home Assistant devices for the physical roles that are
actually present. Entities are associated with the inverter, storage, or meter
rather than merely with the SunSpec model that supplied the value. Repeated
Model 160 modules are classified at runtime as MPPT, storage charging, or
storage discharging data.

### Availability

Initial configuration validates every explicitly configured unit's SunSpec
endpoint. The entry stores model addresses, Common Model identity, and Model
160 module identities so its entities can be constructed without live devices.
No measurements are persisted in this topology cache. Serial-based unique IDs
and existing entity/device identifiers remain unchanged.

An existing entry loads its runtime and platforms even when every unit is
offline. The initial refresh and subsequent 30-second polls handle failures per
unit and per model. Only entities whose current model cannot be read or decoded
become unavailable; stale measurements are discarded. Failed discovery and
reads are attempted again on normal updates, and recovery needs no reload.
Storage and battery availability follows the inverter's storage/module models,
without introducing a separate battery connection.

Entries created before the topology cache was introduced also load offline.
Their registry entries are retained, but missing model addresses and module
identities cannot be reconstructed until the corresponding unit first responds.
Polling discovers and persists that structure and adds its entities automatically.
Subsequent offline starts can construct those entities directly from the cache.
Optional or repeated instances that are not physically present are not invented.

### Troubleshooting

- Confirm SunSpec Modbus TCP is enabled and reachable from Home Assistant.
- Verify the host, port, and Modbus device IDs against the Fronius configuration.
- Check Home Assistant logs for discovery, timeout, or policy-loading errors.
- Reload the integration after changing its write policy.
- If a sensor is missing, verify that the corresponding SunSpec model is
  actually exposed by the device.

Report reproducible issues through the
[issue tracker](https://github.com/ekkehard-lutz/fronius-pv-manager/issues).

## Storage controls (v0.3.0-beta.2)

Seven enabled-by-default controls are the normal storage interface: minimum
reserve (5–100%), grid charging allowed, minimum and maximum charge power,
minimum and maximum discharge power, and automatic/manual/remote operating mode.
An additional read-only Storage control status sensor shows agreement with the
last applied HLC target. Grid permission does not prevent Fronius internal or
service charging.

All four power settings use watts and have the range 0–decoded `WChaMax`, even
when the battery's real technical limit is lower. Initial minima are zero and
initial maxima are `WChaMax` (rounded down if it has fractional watts). User
settings are stored per config entry and Modbus device in Home Assistant's `.storage` directory. They survive restart
and automatic mode; restarting does not automatically apply them to the device.

Automatic mode writes `StorCtl_Mod = 0`, `InWRte = 100%`, and `OutWRte = 100%`.
Editing watt settings in automatic mode only validates and saves them. Manual
mode applies both boundaries (`StorCtl_Mod = 3`) to the signed power interval:

| Settings | InWRte | OutWRte |
| --- | --- | --- |
| Both minima zero | maximum charge / WChaMax × 100 | maximum discharge / WChaMax × 100 |
| Positive minimum charge | maximum charge / WChaMax × 100 | −minimum charge / WChaMax × 100 |
| Positive minimum discharge | −minimum discharge / WChaMax × 100 | maximum discharge / WChaMax × 100 |

A positive minimum forces its direction, excluding the opposite direction;
the opposite maximum remains saved. The two minimum commands are mutually
exclusive: setting a positive minimum charge power clears minimum discharge to
0 W, and setting a positive minimum discharge power clears minimum charge to
0 W. Setting either minimum to zero changes only that field. This lets manual
users and future Energy Manager logic switch forced direction with one HLC
action. The complete normalized settings are validated together and, in manual
mode, applied with one controlled write sequence; in automatic mode they are
only saved. Minima greater than their corresponding maxima and values outside
the reference range are still rejected. Whole-watt settings
are preserved exactly as entered. When applying a manual target, the integration
uses the decoded `WChaMax` and `InOutWRte_SF` to quantize register magnitudes:
maximum constraints round down, minimum constraints round up. The forced-power
sign is applied afterwards, so quantization never weakens a requested boundary.
If the resulting minimum exceeds the resulting maximum, no representable window
exists and the complete request is rejected before any write. Low-level codec
validation remains strict, including live scale-factor validation at preflight.

For `WChaMax = 10240 W` and `InOutWRte_SF = -2`, each raw step is 1.024 W:

| Requested constraint | Raw percentage magnitude | Percentage magnitude | Effective power |
| --- | ---: | ---: | ---: |
| Maximum 1000 W | 976 | 9.76% | 999.424 W |
| Minimum 1000 W | 977 | 9.77% | 1000.448 W |

The HLC still displays and persists 1000 W. The last applied target stores the
quantized register percentages, so status can correctly report `manual_hlc`.
Zero and the ±100% endpoints remain exact for supported rate scales. Scales
that cannot encode the neutral sequence's ±100% endpoints fail before writing.

The number UI uses whole watts with a fixed **1 W Up/Down step**, independently
of `WChaMax` and `InOutWRte_SF`. Hardware resolution may differ: requested
whole-watt values are quantized only when translated to Model 124 register
values. Adjacent watt settings may therefore occasionally produce the same
hardware target. Fractional-watt entries are rejected, and the upper whole-watt
UI bound rounds a fractional `WChaMax` down.

Mode changes and manual edits preflight every policy and encoded register value
before writing. One coordinator I/O lock covers the full sequence and prevents
polling between steps. The sequence disables external limits, broadens both
boundaries to +100%, sets manual boundaries if requested, and enables manual
limits last. Every step requires read-back verification, with one refresh after
success. External limits are temporarily inactive during the transition; this
is not Modbus atomicity and does not exclude other Modbus clients. A failure
stops the sequence, reports verified steps and the uncertain register, and does
not attempt rollback. Saved settings are updated only after verified success.
Inverter and BMS limits remain authoritative.

### Coexistence with expert and external writes

HLC is the normal interface; low-level controls (LLC) are the expert interface
and remain disabled by default. Both integration write paths use the same Write
Policy and coordinator I/O lock. **The last explicit write wins.** An LLC write
cannot interleave with an HLC sequence, but may run immediately afterwards.
Independent external Modbus clients cannot be locked out.

Minimum reserve and grid-charging permission always display confirmed inverter
values, including LLC changes and external changes observed by polling. The
four watt settings and selected HLC operating mode remain independent user
configuration: raw rate/mode writes do not replace them. Initially the selected
HLC mode is automatic; it does not change just because the inverter is manual.
For example, HLC Operating mode may remain manual while status shows an override.

| Storage control status | Meaning |
| --- | --- |
| `automatic` | Confirmed neutral state: mode 0 and both rates +100%. |
| `manual_hlc` | All three registers match the last completely verified HLC manual target. |
| `low_level_override` | A valid confirmed state differs from a known HLC target. This does not identify the writer. |
| `unknown` | Missing/invalid/unsupported data, an impossible active window, or a non-neutral state without a known HLC target. |

Only active boundaries determine whether a window is valid. Neutral automatic
state takes precedence even if the saved HLC mode is manual. During a device
outage Home Assistant marks the sensor unavailable; its classifier returns
unknown until confirmed data is available again.

Polling only observes changes; it never restores HLC targets. Editing a watt
setting in selected manual mode or explicitly selecting manual again validates
all four settings and reapplies the complete window. Editing watts in selected
automatic mode only saves them, even when actual state shows an override.
Explicitly selecting automatic applies the complete neutral target.

The integration persists the selected mode and the last three successfully
applied register values alongside the watt settings. These minimal target values
are needed because changed `WChaMax` or automatic-mode watt edits prevent safely
reconstructing the historical target from settings alone. A partial failure
preserves the previous target and its error details; the next poll classifies
actual state. Startup loads configuration without writing, then classifies the
inverter after reading it. Initial watt-only development storage is retained,
without inventing an applied target. Continuous enforcement belongs to the
future Energy Manager, not these controls.

### Programmatic full windows and remote control

The integration-internal API lives on `entry.runtime_data.storage_control`.
All methods take the configured Modbus `device_id`; owner IDs are coordination
identifiers, not credentials or an additional security boundary. No HA services
or Energy Manager strategy are introduced here.

| API | Behavior |
| --- | --- |
| `async_set_power_window(device_id, settings)` | Atomically replace the complete semantic profile and enter manual mode when not remotely owned. |
| `async_acquire_remote_control(device_id, owner_id, settings=None)` | Apply a complete remote profile, then acquire a 90-second lease. If omitted, use current saved settings. A different live owner is rejected. |
| `async_remote_heartbeat(device_id, owner_id)` | Renew the current live owner's lease without Modbus I/O. |
| `async_set_remote_power_window(device_id, owner_id, settings)` | Apply a complete profile for the live owner; renew only after success. |
| `async_release_remote_control(device_id, owner_id)` | Return to neutral automatic, restore the pre-remote reserve/grid policy and saved power profile, then release ownership. The owner can retry incomplete cleanup. |
| `async_set_remote_minimum_reserve(device_id, owner_id, value)` | Set requested reserve (5–100%) through Write Policy; renew the live owner lease only on success. |
| `async_set_remote_grid_charging_allowed(device_id, owner_id, enabled)` | Set grid permission (boolean) through Write Policy; renew only on success. |
| `remote_owner(device_id)` | Return the live owner ID, or `None`. |

`settings` is a `PowerSettings` object containing all four whole-watt values.
Full-state updates have no implicit directional precedence: callers must supply
zero for the opposite minimum. Both positive minima are rejected. This differs
from an explicit single minimum-field edit, which clears its opposite. A complete
update can change minimum and maximum together (for example, 1000/2000 W to
3000/4000 W) without exposing an invalid intermediate semantic state.

```python
from custom_components.fronius_pv_manager.storage_control import PowerSettings

control = entry.runtime_data.storage_control
profile = PowerSettings(
    minimum_charge_power=1000, maximum_charge_power=2000,
    minimum_discharge_power=0, maximum_discharge_power=10240,
)
await control.async_acquire_remote_control(1, "energy_manager.entry1", profile)
await control.async_remote_heartbeat(1, "energy_manager.entry1")
await control.async_set_remote_power_window(1, "energy_manager.entry1", profile)
await control.async_release_remote_control(1, "energy_manager.entry1")
```

Remote mode applies the same Model 124 semantics as manual (`StorCtl_Mod = 3`).
Only acquisition can enter remote. The HA select displays remote but rejects
normal selection of it; while owned, selection of automatic/manual and writes
to the four HLC power numbers also fail with validation errors. Values remain
visible. Minimum reserve and grid-charging permission are also exclusively
managed by the live remote owner: normal HLC entity/service writes fail explicitly.
Enabled LLC entities retain their existing Write Policy behavior.

Remote updates share the same complete validation, exact quantization, locked,
ordered and read-back-verified sequence as manual updates. Atomicity here means
one semantic state update, **not atomic Modbus writes**. Remote commands update live settings only after complete success; persistence
retains the pre-remote user power profile. Cleanup records verified Automatic
power safety even if a later restoration step fails. `manual_hlc` status also denotes
agreement with a remotely applied HLC profile; it describes register agreement,
not ownership. LLC/external overrides remain visible, and a heartbeat never
restores a target. Only an explicit profile command reapplies it.

The fixed lease timeout is **90 seconds**, measured with a monotonic clock.
A future controller should send heartbeats around every 30 seconds. A one-shot
HA timer, renewed by heartbeat or a successful remote command, returns storage
to the complete automatic target on expiry and logs a warning. Release likewise
always returns to automatic. There is no periodic register rewrite or control
loop. PV Manager owns device-level control; a future separate Energy Manager
owns the closed-loop strategy.

Remote takeover is temporary. One immutable in-memory `PreRemoteSnapshot` per
device contains `minimum_reserve`, `grid_charging_allowed`, and all four
`power_settings`. Reserve and grid permission are read live under the I/O lock,
after takeover preflight and immediately before its first write. The snapshot
becomes active only after takeover succeeds. Same-owner reacquisition and remote
updates do not replace it; a new lease after cleanup captures new user values.

Explicit release and watchdog timeout use the same deterministic sequence:

1. Check authority and the snapshot; preflight all register writes.
2. Write and verify `StorCtl_Mod = 0`, `InWRte = +100%`, `OutWRte = +100%`.
3. Restore and verify the original minimum reserve, then grid permission.
4. Restore the four original PowerSettings to persistence and the displayed
   user profile, without writing them as manual register limits.
5. Clear the owner, timer, and snapshot; remain in Automatic.

**Previous manual mode is never restored.** A later explicit user selection of
manual applies the restored profile. The five hardware writes share the I/O
lock and normal Write Policy/encoding/read-back checks; they are not a Modbus
transaction. Reserve/grid preflight errors are discovered before any write but
reported after the neutral safety steps, so they do not prevent an otherwise
valid Automatic transition. No denied restoration value is written.

If neutral verification fails, mode and last target retain their prior confirmed
values. If neutral verifies but reserve/grid restore fails, runtime mode and last
target record Automatic; no remote/manual limits are reapplied. Execution stops
at the failed step, which may have applied if its verification failed. If profile
persistence fails, the hardware can already be fully restored, but the live power
profile remains the remote profile until a successful retry.

Incomplete cleanup retains the immutable snapshot and owner reservation plus
runtime `RemoteCleanup(verified_steps, failed_step)` diagnostics. It has **no live
owner** and cannot heartbeat or accept remote updates. The watchdog is cancelled;
there is no unattended retry loop. Errors propagate to explicit callers and are
logged for watchdog expiry. The original owner may retry release; a later manual
action or acquisition first retries complete cleanup. Retries begin with neutral
Automatic again, never with old manual limits. Only complete cleanup clears the
snapshot and reservation. There is no speculative rollback.

Owner, heartbeat, cleanup diagnostics, and the reserve/grid snapshot are
runtime-only. The original user power profile remains persisted during remote
operation; temporary remote profiles do not replace it. No snapshot is serialized
or restored across a Home Assistant restart. On restart, saved remote mode becomes
semantic automatic, with no active owner and **no startup write**. The first poll
observes actual hardware; it may still have the previous manual/remote register
window. Historical target metadata remains available for status comparison.
Selecting automatic explicitly releases that hardware state; a new remote
controller must acquire a fresh lease. Unload cancels watchdogs and drains
pending HLC operations before closing transport; it does not add an unload
write or restore ownership on the next setup.

## For advanced users: low-level register controls

NUMBER and SELECT entities expose safely representable low-level writable
registers from SunSpec Models 123 and 124. They are configuration entities and
are disabled by default in Home Assistant's Entity Registry. They are not the
normal user interface for storage or inverter control.

Disabled LLC entities are a UI default, **not an authorization or security
boundary**. The integration has no separate expert-user permission mechanism.
A user with sufficient Home Assistant permission can enable an LLC entity and
write its register subject to Write Policy and register validation.

These settings have different purposes:

1. **Entity Registry state** controls whether an entity is visible in Home
   Assistant. Disabling an LLC entity does not prevent internal HLC writes.
   Enabling it exposes direct writes for any register allowed by Write Policy.
2. **Installation write policy** explicitly permits or denies writes in
   `/config/fronius_pv_manager/write_policy.yaml`. An enabled policy means the
   integration is technically allowed to write the register. HLC internal writes
   also require the underlying register to be enabled in this policy.
3. **Register semantics** enforce authoritative access, datatype, range, enum,
   bitfield, scaling, and representation constraints. Policy can narrow these
   constraints but cannot broaden them.

The integration copies the packaged default policy on first setup and never
overwrites an existing installation policy. Policy edits take effect after an
integration reload or Home Assistant restart. A missing, disabled, or invalid
policy fails closed: readable entities and polling remain available, but writes
are rejected before Modbus I/O. Invalid policy never falls back to permissive
defaults.

The packaged policy explicitly lists all 25 writable Model 123/124 registers.
Model 124 `MinRsvPct` (policy range 5–100%), `ChaGriSet`, `StorCtl_Mod`,
`InWRte`, and `OutWRte` are write-enabled by default. All low-level writable
entities remain disabled in the Entity Registry. Existing installation policies
are intentionally not migrated in this beta; during development, delete the
installation policy manually and reload to recreate the new default.

### Storage control and forced charging/discharging

These are low-level power-window controls, not direct charge/discharge commands:

| Register | Meaning |
| --- | --- |
| `WChaMax` | SunSpec reference power used to scale `InWRte` and `OutWRte`; it is not guaranteed to be currently achievable battery power. |
| `StorCtl_Mod` | Selects which external storage-power window boundaries are active. |
| `InWRte` | Charge-side boundary as a percentage of `WChaMax`. |
| `OutWRte` | Discharge-side boundary as a percentage of `WChaMax`. |
| `ChaGriSet` | Determines whether grid energy may be used for charging. |

`StorCtl_Mod` uses these values:

| Value | Presentation | Effect |
| ---: | --- | --- |
| 0 | Automatic | Neither `InWRte` nor `OutWRte` limit is active; normal inverter control applies. |
| 1 | Charge limit active | `InWRte` applies. |
| 2 | Discharge limit active | `OutWRte` applies. |
| 3 | Charge and discharge limits active | Both power-window boundaries apply. |

Fronius storage-power sign convention:

- Negative storage power means charging.
- Positive storage power means discharging.

Conceptual hardware-validated examples:

- To force discharge at around 10% of `WChaMax`, use `StorCtl_Mod = 1` and
  `InWRte = -10%`. `OutWRte` is not active in this mode.
- To require charging of at least around 10% of `WChaMax`, use
  `StorCtl_Mod = 2` and `OutWRte = -10%`. `InWRte` is not active in this mode.
  `ChaGriSet` must permit grid charging if grid energy is required.

The charging example establishes a boundary/minimum forced-charging condition,
not an exact charging-power command. Available PV surplus can result in higher
charging power. Inverter and BMS constraints remain authoritative and can reduce
actual power. Restore `StorCtl_Mod` to `0` to return to normal automatic inverter
operation.

> **Warning:** Low-level writes can materially alter inverter and storage
> behavior. Understand every affected register, record original values, start
> conservatively, verify the result, and restore the original configuration
> after testing.

## For software developers

### Generic capability-based design

The project deliberately avoids product-name-specific code when SunSpec models
and capabilities can describe the behavior generically:

`physical device → SunSpec discovery → discovered models/registers → reviewed RegisterDefinition/model metadata → semantic role/capability mapping → entity catalog → Home Assistant entities`

A new product may work without product-specific code when it exposes already
supported models with compatible semantics. New functionality should normally
extend reviewed model, register, and capability definitions instead of adding
product-name conditionals.

Product-specific exceptions are appropriate only when actual behavior differs
from the general SunSpec definition and authoritative documentation or hardware
evidence supports the exception. Existing GEN24-specific handling follows this
rule; it is not a product-specific architecture.

### Architecture

The Home Assistant-independent model layer provides immutable `DeviceProfile`,
`RegisterDefinition`, model, repeating-block, and decoded-value types. Reviewed
register maps describe protocol semantics and neutral entity presentation
metadata.

The runtime layers are:

1. SunSpec discovery verifies the `SunS` signature and walks the model chain.
2. Model decoding converts payloads through the reviewed register definitions.
3. `ModbusTcpEndpointTransport` owns one persistent pymodbus client/socket for
   each configured host and port.
4. Bound per-device-ID views share that endpoint while attaching the device ID
   to every request.
5. The coordinator serializes polling and writes, publishes decoded snapshots,
   and preserves partial availability across device IDs.
6. The entity catalog creates sensor, NUMBER, and SELECT entities on the
   appropriate physical Home Assistant device.
7. `WriteRuntime` resolves policy and topology, while `RegisterWriter` performs
   semantic encoding, exactly one physical write, readback, and verification.

Important guarantees:

- One shared TCP client/socket per configured host and port.
- Polls and writes are serialized across bound device-ID views.
- Failed requests reset the endpoint so later operations can reconnect.
- Uncertain writes are not retried automatically.
- Every accepted entity operation performs at most one physical write.
- Readback verification is required before coordinator refresh.
- Coordinator state is never changed optimistically.
- Home Assistant exposes no arbitrary raw-register write service.

### Developer tools

Read one semantic register without write-policy dependency:

```bash
python tools/read_register.py --host 192.168.2.11 --device-id 1 --parameter 124:MinRsvPct
```

Prepare a validated write in dry-run mode:

```bash
python tools/write_register.py --host 192.168.2.11 --device-id 1 --parameter 124:MinRsvPct --value 10
```

An actual developer write additionally requires `--write` and confirmation.
These tools resolve reviewed semantic register definitions; they are not generic
raw Modbus clients.

### Contributing

Contributions for additional Fronius inverters, storage systems, Smart Meters,
and SunSpec capabilities are welcome, especially when contributors can validate
hardware unavailable to the maintainers. Acceptance still depends on reviewed
semantics, architecture, safety, tests, documentation, and supporting evidence.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch model, validation commands,
and pull-request workflow.

### Validation

Development targets Python 3.14. Run:

```bash
python -m ruff check .
python -m pytest
git diff --check
```

## License

Fronius PV Manager is licensed under the [MIT License](LICENSE).


### Temporary remote hardware-test actions (development only)

These actions are a **temporary test harness**, to be removed before stable
v0.3.0. They call the same storage-control APIs intended for the future Energy
Manager, using the fixed owner ID `fronius_pv_manager.debug_test`. They do not
accept an owner ID from service input.

All actions require integer `device_id` (the Modbus unit ID, 1–247).
Optionally supply `config_entry_id` to select an endpoint; it is required when
the unit ID is shared by multiple loaded entries. Unknown or ambiguous routing
fails before a write. Actions exist only while at least one entry is loaded.

| Action (domain `fronius_pv_manager`) | Additional inputs |
| --- | --- |
| `debug_remote_acquire` | Optionally all four power fields; omit all to use saved settings. |
| `debug_remote_set_window` | All four power fields, required. |
| `debug_remote_heartbeat` | None; renews without Modbus writes. |
| `debug_remote_release` | None; returns to verified automatic operation. |
| `debug_remote_set_minimum_reserve` | `value`: 5–100 percent, subject to Write Policy. |
| `debug_remote_set_grid_charging_allowed` | `enabled`: boolean. |

The four fields are `minimum_charge_power`, `maximum_charge_power`,
`minimum_discharge_power`, and `maximum_discharge_power`, in whole watts.
A complete profile must be coherent; both minima cannot be positive.

For example, call acquire, change the window/reserve/grid permission as needed,
send heartbeat at intervals shorter than 90 seconds (for example 30 seconds),
then release. Stopping heartbeats exercises the existing automatic watchdog
fallback. Failed reserve/grid writes do not renew the lease. Release or expiry
returns to Automatic and restores the original reserve, grid permission, and
saved user power profile through the same real snapshot cleanup path.
Fronius may maintain an effective reserve above the requested minimum, and its
internal safety/service charging can occur independently of grid permission.
