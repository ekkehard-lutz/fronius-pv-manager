# Fronius PV Manager

Fronius PV Manager is a local-polling Home Assistant custom integration for
Fronius systems that expose SunSpec over Modbus TCP. It discovers supported
inverter, storage, and Smart Meter capabilities and organizes their entities as
separate Home Assistant devices.

Fronius PV Manager v1.0.1 is a documentation and release-history cleanup of
v1.0.0, the first official stable release. Functionality includes high-level
storage controls and the programmatic remote-control interface. PV Manager
supplies device-near validation and control; tariff optimization, scheduling,
and continuous strategy belong to a future Energy Manager. It does not
continuously reassert targets.

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

The hardware-validation reference setup is:

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

For manual installation, copy `custom_components/fronius_pv_manager` into
Home Assistant's `custom_components` directory and restart. After replacing
integration code through HACS or manually, restart Home Assistant. Reload the
entry for configuration/policy changes; recovery from an ordinary device outage
does not require a reload.

### Configuration

Before configuration, enable SunSpec Modbus TCP on the Fronius equipment using
the vendor documentation and select **integer + scale factor (int + SF)** as
the SunSpec representation. The supported maps are Common Model 1, three-phase
inverter 103, nameplate/settings/status/controls 120–123, storage 124,
multiple-MPPT 160, and three-phase meter 203. Floating-point inverter/meter
variants are not implemented; selecting them can leave measurements missing.

Then in Home Assistant:

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

### Entity overview

| Group | Contents and defaults |
| --- | --- |
| Inverter | Supported power, energy, electrical, status and diagnostic measurements; operational sensors enabled, technical diagnostics generally disabled. |
| Smart Meter | Supported three-phase meter measurements on its configured unit ID. |
| Storage | Supported battery measurements and Model 160 storage channels when discovered. |
| LLC | Model 123/124 register Number/Select controls; disabled by default. |
| HLC | Seven enabled controls and the read-only Storage control status sensor, described below. |
| Solar API | Backup mode on inverter; Battery operation mode and Battery standby only on a discovered storage role. Enabled semantic entities; independently unavailable when API data is missing. |

Entity creation follows supported discovered capabilities, not product names.

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

Cached logical topology constructs offline entities, but never authorizes a
physical write. Each runtime discovers live addresses before polling registers
or writing. Connection resets and failed model reads invalidate live authority;
normal scheduled polls retry discovery. Live validation expires after five
minutes; the next poll revalidates it. Healthy polls do not each scan the
model chain. Writes reject expired/unvalidated topology until polling restores
it. A changed live layout updates the persisted cache without replaying controls.
Prepared writes cannot continue after their live addressing authority changes,
including a connection reset during cleanup preparation. Cleanup retains its
retry state; after discovery recovers, explicitly retry release. It does not
rebuild or replay the failed sequence automatically.

### Troubleshooting

- Confirm SunSpec Modbus TCP is enabled and reachable from Home Assistant.
- Verify the host, port, and Modbus device IDs against the Fronius configuration.
- Check Home Assistant logs for discovery, timeout, or policy-loading errors.
- Reload the integration after changing its write policy.
- If measurements are missing, verify **int + SF** and the supported model IDs.
- If only Solar entities are unavailable, enable the local Solar API and check
  HTTP reachability; Modbus availability is independent.
- For policy rejection, inspect the named register in the installation policy;
  preserve local restrictions when comparing with the packaged default.
- Invalid HLC windows need minima no greater than maxima, at most one positive
  minimum, and values within WChaMax. A narrow window may be impossible after
  inward quantization; widen it instead of bypassing validation.
- Remote ownership rejects competing HLC commands. Coordinate with the current
  owner; expiry or failed cleanup may require an explicit release retry.
- Debug logs for `custom_components.fronius_pv_manager`, the exact failed phase,
  configured IDs, firmware, and exposed model IDs help reproduce issues.
  Do not include credentials or unrelated Home Assistant storage files.

Report reproducible issues through the
[issue tracker](https://github.com/ekkehard-lutz/fronius-pv-manager/issues).

## Storage controls

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
Explicit Automatic does not require WChaMax or create a default watt profile;
it still requires valid control scale factors, live topology and Write Policy.
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
inverter after reading it. Legacy watt-only storage is retained without
inventing an applied target. Continuous enforcement belongs to the
future Energy Manager, not these controls.

For programmatic ownership and complete power windows, see the
[Developer API](#developer-api).

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
are never automatically migrated. Back up your policy, compare changes with
the packaged default, and merge only permissions/ranges appropriate for your
installation before reloading. Do not delete local restrictions as routine
upgrade advice.

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
- Each individual register operation makes at most one physical write attempt.
  HLC actions use ordered, read-back-verified multi-register sequences.
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
raw Modbus clients. The standalone write tool runs outside the integration and
does **not** load the Home Assistant installation Write Policy. Its explicit
confirmation and register validation are separate safeguards; the integration
cannot lock or own its independent Modbus session.

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

## Developer API

### Remote storage control / Energy Manager integration

Fronius PV Manager owns device-near safe storage control: validation, Write
Policy, SunSpec encoding, serialized I/O, ordered writes, and read-back
verification. A future Energy Manager owns strategy and closed-loop control.
It should use this programmatic API rather than manipulate HLC entities.
Low-level register entities are not the Energy Manager API. There are no
temporary HA debug actions or an Energy Manager implementation in this project.

Remote control is a **temporary exclusive lease per device**, not a hardware
lock or security boundary. All calls are awaited on Home Assistant's event loop.

### Access and identifiers

Obtain the relevant **loaded Fronius PV Manager config entry**, then use:

```python
from custom_components.fronius_pv_manager.storage_control import PowerSettings

control = entry.runtime_data.storage_control
```

The caller must select the correct entry/endpoint when multiple entries exist.
Do not retain this runtime object across entry unload/reload; obtain the new
loaded entry's runtime before making further calls.

- `device_id` is the configured integer Modbus/SunSpec unit ID for the
  inverter/storage device, **not a Home Assistant device registry ID**. The same
  unit ID can exist on different endpoints, so the entry is part of routing.
- `owner_id` is a nonempty, stable string chosen by the Energy Manager, for
  example `"energy_manager.my_entry"`. Keep it consistent throughout a lease.
  It is a coordination identifier, not a secret or authentication credential.

### Public compatibility surface

The intended public interface is `entry.runtime_data.storage_control`,
`PowerSettings` with the four named fields below, and the eight control/ownership
methods in the table. Mutable maps, coordinator snapshots, prepared plans,
lease objects, pre-remote snapshots and `RemoteCleanup` are implementation
details. Do not import or mutate them in an Energy Manager.

`ServiceValidationError` is the supported validation/ownership rejection
boundary. At the external control-call boundary, handle other `Exception`
failures as unsuccessful operations: transport, readback, refresh and storage
errors may propagate, including errors while capturing the pre-remote snapshot.
Log and surface the failure rather than assuming an unchanged device or retrying
blindly. Individual internal exception subclasses and message text are not a
compatibility promise. Preserve `asyncio.CancelledError`; cancellation can
arrive after physical I/O completed and is not proof hardware stayed unchanged.

### Supported methods

Async methods below complete normally with no return value; failures raise.
`remote_owner` is synchronous.

| Method | Contract |
| --- | --- |
| `async_set_power_window(device_id, settings)` | Apply a complete profile and explicitly enter manual mode without a live remote owner. This is the non-remote complete-window entry point. |
| `async_acquire_remote_control(device_id, owner_id, settings=None)` | Apply a complete remote profile, acquire exclusive ownership, and start/renew the 90-second lease. |
| `async_remote_heartbeat(device_id, owner_id)` | Renew the current live owner's lease without Modbus I/O. |
| `async_set_remote_power_window(device_id, owner_id, settings)` | Apply a complete profile for the current live owner; renew only after success. |
| `async_set_remote_minimum_reserve(device_id, owner_id, value)` | Write requested reserve, 5–100%, through Write Policy; renew only after success. |
| `async_set_remote_grid_charging_allowed(device_id, owner_id, enabled)` | Write boolean grid permission through Write Policy; renew only after success. |
| `async_release_remote_control(device_id, owner_id)` | Return to neutral Automatic, restore the pre-remote policy/profile, then release ownership. The original owner may retry incomplete or expired cleanup. |
| `remote_owner(device_id)` | Return the live owner string, or `None` when unowned, expired, or cleanup is pending. This does not initiate I/O. |
| `async_shutdown()` | Integration lifecycle cleanup: reject new requests, cancel timers, drain pending HLC I/O, and discard runtime leases/snapshots. It does not write a release target. PV Manager calls this on unload and HA stop; an Energy Manager should release its lease instead of shutting down the shared runtime. |

### PowerSettings and watt semantics

Every complete update supplies this exact structure:

```python
settings = PowerSettings(
    minimum_charge_power=0,
    maximum_charge_power=5000,
    minimum_discharge_power=0,
    maximum_discharge_power=6000,
)
```

These example values must fit the connected device's decoded `WChaMax`.
All four fields are finite, nonnegative **whole watts**, including API input;
booleans, fractional watts, and non-finite values are rejected. Integral floats
are accepted and normalized to integers. Each value must be at most `WChaMax`,
and each minimum must be at most its corresponding maximum.

| Field | Meaning |
| --- | --- |
| `minimum_charge_power` | Positive values command forced charging with this minimum magnitude. Zero imposes no forced charging minimum. |
| `maximum_charge_power` | Upper charging constraint. Zero prohibits regular charging within this power window. |
| `minimum_discharge_power` | Positive values command forced discharging with this minimum magnitude. Zero imposes no forced discharging minimum. |
| `maximum_discharge_power` | Upper discharging constraint. Zero prohibits regular discharging within this power window. |

The two minima cannot both be positive. A complete `PowerSettings` update has
**no implicit precedence**: when changing forced direction, explicitly put zero
in the opposite minimum. The HA single-minimum field path has its own
opposite-minimum normalization; complete API profiles must already be coherent.
Zero minima do not mean zero battery power, and zero maxima do not select
Automatic mode. Actual battery power may differ from the constraints because of
PV production, load, SOC, BMS, inverter limits, and Fronius safety behavior.

Semantic watts are distinct from representable hardware values. Model 124 uses
percentage rates with the actual SunSpec `InOutWRte_SF`. PV Manager converts
against decoded `WChaMax` using exact Fraction-based arithmetic: maximum
constraints quantize downward, minimum magnitudes upward, and signs are applied
after magnitude quantization. A minimum charge command maps to negative
`OutWRte`; a minimum discharge command maps to negative `InWRte`.
A window that becomes impossible after quantization is rejected before writing.

UI resolution remains 1 W. Adjacent semantic watt values may produce the same
raw target; exact requested watt realization is not promised. For the tested
`WChaMax=10240 W`, `InOutWRte_SF=-2`, a 1000 W maximum has raw magnitude
976, while a 1000 W minimum has raw magnitude 977.

### Acquisition and complete remote updates

`async_acquire_remote_control(device_id, owner_id, settings=None)` applies a
complete profile and enters `remote`, using `StorCtl_Mod=3` limit semantics.
A different live owner is rejected. On initial acquisition, omitting settings
uses the current saved user PowerSettings. Same-owner reacquisition is also an
explicit profile write and lease renewal: if settings are omitted during an
existing lease, it uses the current live remote profile, preserving the original
snapshot.

Immediately before the first takeover write, reserve and grid permission are
read live under the coordinator I/O lock, after write preflight. Together with
the original user PowerSettings they form one immutable runtime-only snapshot.
It becomes active only after successful takeover. It contains no operating mode
that will later be restored. Failed initial acquisition creates no valid lease
or snapshot; failed same-owner reacquisition preserves the existing snapshot
and does not renew the lease. A partial hardware write can still have occurred.

`async_set_remote_power_window(device_id, owner_id, settings)` requires the
current live owner and all four settings. The complete-window API lets a caller
change multiple limits in one semantic transition, such as minimum/maximum
1000/2000 W to 3000/4000 W, without an invalid intermediate semantic profile.
Remote updates change the live profile while persistence retains the original
user profile. Neither remote updates nor same-owner acquisition replace the
snapshot. A new lease after complete release captures fresh user values.

The semantic profile update is atomic from the API's point of view; the
underlying Modbus writes are **not transactionally atomic**. Validation and
encoding preflight happen before writes where possible. The existing ordered
sequence passes through neutral control before applying the final signed rates
and mode 3, with read-back verification under the shared I/O lock. This is one
controlled sequence, not four independent HA Number writes.

### Heartbeat and policy commands

The lease lasts **90 seconds**, measured with a monotonic clock. Schedule
`async_remote_heartbeat(device_id, owner_id)` approximately every **30 seconds**.
Only the current live owner may heartbeat. Heartbeat renews the lease without
Modbus reads or writes and never reapplies a power target. Successful remote
power, reserve, and grid commands also renew the lease; failed commands do not.
An expired lease cannot be renewed by heartbeat or a remote update.

`async_set_remote_minimum_reserve(device_id, owner_id, value)` requires the
current live owner and a finite numeric value in **5–100%**. Write Policy and
register representation can restrict the request further. The effective lower
SOC may be higher because of Fronius internal reserve settings not exposed by
Modbus. `MinRsvPct` is not a guaranteed Full Backup lower bound.

`async_set_remote_grid_charging_allowed(device_id, owner_id, enabled)` requires
the current live owner and a boolean. It maps to `ChaGriSet` (false=0, true=1)
through normal validation and Write Policy. This is regular Modbus grid-charging
permission; Fronius safety/service charging may still occur independently.

### Modes, exclusivity, and external writes

| Operating mode | Meaning |
| --- | --- |
| `automatic` | Fronius internal control with the neutral target `StorCtl_Mod=0`, `InWRte=+100%`, `OutWRte=+100%`. |
| `manual` | Explicit user HLC control, with `StorCtl_Mod=3` and the saved profile translated to signed limits. |
| `remote` | Programmatic ownership with the same Model 124 limit semantics as manual. Normal HA Operating Mode selection cannot enter remote. |

While a live remote owner exists, normal HLC writes to Operating Mode, Minimum
Charge Power, Maximum Charge Power, Minimum Discharge Power, Maximum Discharge
Power, Minimum Reserve, and Grid Charging Allowed raise
`ServiceValidationError`. Values remain visible; entities are not disabled.

Enabled low-level entities remain governed by Write Policy and are not
technically blocked by the lease. External Modbus clients cannot be locked out.
**Last explicit write wins at the hardware level.** Polling observes overrides
without continuously reasserting the remote target. An Energy Manager that
wants to reclaim a desired state must explicitly send a new remote command.
The `manual_hlc` status can also indicate agreement with a remote HLC target;
it describes register agreement, not ownership.

### Release, expiry, and failure handling

Both `async_release_remote_control(device_id, owner_id)` and watchdog expiry
use the same cleanup path:

1. Check authority/snapshot and preflight all register writes.
2. Write and verify neutral Automatic: `StorCtl_Mod=0`, `InWRte=+100%`,
   `OutWRte=+100%`.
3. Restore and verify pre-remote Minimum Reserve.
4. Restore and verify pre-remote Grid Charging Allowed.
5. Restore the original PowerSettings in persistence and the displayed user
   profile, without writing them as manual Model 124 limits.
6. Clear the owner, watchdog, and snapshot.

Successful cleanup **always ends in Automatic**. The previous manual mode is
never automatically restored; entering manual again requires a later explicit
user action. The restored PowerSettings are the saved future manual profile.
While the lease is valid, the watchdog causes no periodic Modbus writes. Only
expiry initiates cleanup.

Automatic power safety has highest priority. All five hardware steps share
normal Write Policy, encoding, I/O locking, and read-back verification.
Reserve/grid preflight errors are discovered before any write but reported
after the neutral safety steps: they do not prevent an otherwise valid Automatic
transition, and no denied restoration value is written.

There is **no speculative rollback**. If neutral verification fails, runtime
mode and last target retain prior confirmed values. If neutral verifies but
reserve/grid restoration later fails, runtime mode and last target record
Automatic. Old remote/manual limits are never reapplied because of that failure.
Execution stops at the failed step; a write whose verification failed may have
applied. A later poll provides fresh observed hardware state.

If all writes verify but the final refresh fails, complete cleanup is still
reported as failed, with verified hardware progress retained. If profile
persistence fails, the hardware may be fully restored but the live profile
remains the remote profile until retry succeeds. Neither case claims complete
restoration.

Home Assistant Store can log a failed write without raising. PV Manager saves
a unique revision and loads through a separate read-only Store to check that
the complete document is visible through HA's storage layer. A mismatch or
read failure is a persistence failure and retains cleanup retry information.
This is observed persistence, **not a guarantee of fsync or power-loss
durability**. Deferred shutdown saves are not treated as confirmed merely
because `async_save()` returned. All storage access uses HA Store; no direct
writes into HA storage files are made.

Incomplete cleanup retains the snapshot, an owner reservation, and internal
diagnostics recording the number of verified register steps and the failed
register or phase. The internal `RemoteCleanup` dataclass and private maps are
not supported public APIs. `remote_owner(device_id)` returns `None` during
pending cleanup; this alone does not prove that all policy restoration succeeded.
Heartbeats and remote updates are rejected, the watchdog is cancelled, and
there is **no unattended retry loop**. The original owner may explicitly retry
release. A later manual action or acquisition first retries complete cleanup.
Retries begin with neutral Automatic, never old manual limits.

Validation/ownership errors raise `ServiceValidationError`; write-policy,
transport, and verification failures can raise write-runtime exceptions, and
persistence failures can raise storage/I/O exceptions. Do not treat a raised
command as a successful lease renewal or assume hardware was unchanged.
Explicit cleanup failures propagate to the caller; watchdog failures are logged.
An Energy Manager should surface failures and coordinate explicit recovery
rather than continuously retrying failed cleanup.

### Restart, unload and Home Assistant stop

Remote ownership, heartbeat timer, pre-remote snapshot, and cleanup diagnostics
exist only in memory. After Home Assistant restart, none is restored and no
snapshot restoration write occurs. The original user power profile remains
persisted, but persisted `remote` mode is interpreted as semantic Automatic.
Startup first observes actual hardware, which may still contain previous
external/manual Model 124 values. Semantic Automatic at startup does not prove
that a neutral target has already been written.

A new Energy Manager instance must acquire a fresh lease. PV Manager unload
cancels timers, drains pending HLC operations, and discards runtime ownership
without adding an unload write. An orderly Energy Manager shutdown should
explicitly release while the PV Manager entry is still loaded.

HA stop independently rejects new operations, cancels watchdogs, drains
already-started synchronous client I/O, and closes each endpoint once.
Shutdown is not remote release: it never writes Automatic or restores policy.
Repeated unload/stop is safe. Cancellation cannot release the shared I/O lock
while its executor worker still owns the client.

### Example Energy Manager flow

This pseudocode illustrates orchestration, not an Energy Manager implementation.
Choose a profile within the connected device's range. Run the heartbeat
independently enough that a quiet strategy loop does not accidentally lose its
lease; propagate heartbeat failures to the controlling task.

```python
import asyncio
from custom_components.fronius_pv_manager.storage_control import PowerSettings

control = entry.runtime_data.storage_control
device_id = 1  # Configured Modbus unit ID on this entry's endpoint.
owner_id = "energy_manager.my_entry"
settings = PowerSettings(
    minimum_charge_power=0, maximum_charge_power=5000,
    minimum_discharge_power=0, maximum_discharge_power=6000,
)

async def heartbeat_until_stopped(stop):
    while not stop.is_set():
        await control.async_remote_heartbeat(device_id, owner_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass

await control.async_acquire_remote_control(device_id, owner_id, settings)
try:
    async with asyncio.TaskGroup() as tasks:
        stop = asyncio.Event()
        tasks.create_task(heartbeat_until_stopped(stop))
        try:
            async for complete_settings in strategy_updates():
                await control.async_set_remote_power_window(
                    device_id, owner_id, complete_settings
                )
                # Optional owner-checked policy commands:
                # await control.async_set_remote_minimum_reserve(
                #     device_id, owner_id, 25
                # )
                # await control.async_set_remote_grid_charging_allowed(
                #     device_id, owner_id, True
                # )
        finally:
            stop.set()
finally:
    await control.async_release_remote_control(device_id, owner_id)
```

Here `strategy_updates()` is supplied by the future Energy Manager. Acquisition
is outside the release `try/finally` so failed acquisition is not mistaken for
ownership. Production code must report acquisition, heartbeat, command, and
release failures, and ensure the entry remains loaded while releasing.

### Reported GEN24 hardware verification

Successful real GEN24 hardware testing reported verification of remote acquire,
HLC exclusivity, complete remote power-window updates, heartbeat lease renewal,
remote Minimum Reserve and Grid Charging Allowed writes, and explicit release.
Both explicit release and watchdog timeout ended in Automatic and restored the
pre-remote reserve, grid permission, and saved user power profile. These results
apply to the tested GEN24 setup; they do not guarantee identical behavior across
all firmware, batteries, devices, or operating conditions.

## Energy Dashboard power sensors

Three enabled, read-only high-level sensors use existing decoded Modbus data:

| Sensor | Physical device | Source |
| --- | --- | --- |
| PV Power | Inverter | Sum of all currently available Model 160 MPPT `DCW` values. |
| Grid Import Power | Meter | `max(W, 0)` from Model 203 signed AC active power. |
| Grid Export Power | Meter | `max(-W, 0)` from Model 203 signed AC active power. |

PV Power excludes storage charge/discharge and unknown modules using the existing
Model 160 classification. It never uses aggregate inverter DC power, which can
include battery power on GEN24. Any number of discovered MPPTs is supported;
missing MPPT values are omitted, and no available MPPT values means unavailable.
A valid zero remains zero. Failed device/model polls do not supply stale values.

Grid directions follow the [Fronius meter convention](https://manuals.fronius.com/html/4204102649/en-US.html)
for a meter at the grid connection: positive means import, negative means export.
Select the grid-connection meter for Energy Dashboard use; a load or generator
meter measures a different flow. Missing meter power makes both sensors
unavailable. Both directions are non-negative and cannot be positive together.

PV Power plus Grid Import/Export Power are suitable for Home Assistant Energy
Dashboard **power-flow configuration**. All use W, device class `power`, and
state class `measurement`; energy totals remain separate sensors. Suggested
object IDs are `inverter_pv_power`, `meter_grid_import_power`, and
`meter_grid_export_power`, independent of UI language. English and German display
names are provided. Entities also appear when topology is discovered after
startup. These sensors need no Solar API access and leave raw sensors unchanged.

## Optional local Fronius Solar API

SunSpec/Modbus TCP remains the primary interface. The optional local Solar API V1
supplements only semantic states absent from the supported Modbus models:

- **Battery operation mode** (storage device)
- **Backup mode** (inverter device)
- **Battery standby** (storage device)

Enable the local Solar API in the inverter web interface to use these entities.
The integration reads `/solar_api/v1/GetPowerFlowRealtimeData.fcgi` on the
configured inverter host; no cloud connection or Solar API writes are used.
No Modbus power, energy or SOC measurements are replaced.

If the API is disabled or unreachable, only these three entities are unavailable;
Modbus measurements and storage controls continue normally. Requests use the
existing polling cadence with a three-second timeout and automatic recovery,
without a reload. Missing or invalid fields affect only their own entities.
Entities follow cached/discovered inverter topology, including later discovery.
Battery modes use the configured SunSpec device ID to select the inverter entry.
This ID equality is a current routing assumption, covered by synthetic tests;
the repository does not establish it for arbitrary nondefault hardware IDs.
Verify the Solar API inverter keys on such installations before relying on the
battery semantic states.
Known mode values have English/German display translations. Unknown future
`Battery_Mode` strings are intentionally shown unchanged until explicit
translations are added. Empty or whitespace-only mode strings are unavailable.

## Fronius reserve and backup limitations

Requested power windows are constraints, not guaranteed physical battery power.
Fronius and BMS safety/service behavior remains authoritative. Review reserve
capacity in the Fronius web interface separately from Modbus `MinRsvPct`;
internal reserve constraints can take precedence and no universal reserve
percentage is recommended here. Grid permission governs regular controlled
charging, not every safety/service charge.

Smart Meter reachability during Full Backup depends on wiring and installation.
An unavailable meter affects its own entities and recovers through polling.
The repository does not record enough firmware/wiring-specific Full Backup
evidence to generalize this behavior or claim a guaranteed backup reserve.
The GEN24 remote-control verification above is not a Full Backup certification.
