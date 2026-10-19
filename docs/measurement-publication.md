# Measurement publication and Recorder assessment

## Architecture review (base: develop, v1.2.0)

`coordinator.py` owns one FAST coordinator, per-unit monotonic SLOW deadlines,
whole-model reads and the existing cancellation-safe I/O lock. `transport.py`
serializes endpoint requests and resets failed TCP sessions with a new generation.
`model_decoder.py`, `codec.py` and Python `register_maps/` apply SunSpec scale
factors before publication. Models 103/124/160/203 remain FAST; 1/120/121/122/123
remain SLOW. A model is committed only after all its 100-register chunks decode.

`ModelCoordinatorEntity` already suppressed unchanged source objects and
availability. This remains the mechanism for raw sensors/numbers/selects; only
sensors select the retained `measurement_data` view. Successful equal FAST values
still have a new snapshot and report freshness. Live `data` preserves the original
failure semantics for `storage_entity.py`, `storage_control.py`, `write_runtime.py`
and low-level controls. None consumes a sensor's normalized value or efficiency.
No efficiency value feeds validation, HLC targets, leases or physical writes.

`solar_entity.py` handles topology/lifecycle and optional HTTP statuses.
`MeasurementSolarEntity` handles only read-only derived sensor publication. Its
formula properties still use original live values; publication caches the last
complete calculation. All FAST dependencies must have new successful whole-model
snapshots before a new calculation/sample is accepted. A failed attempted SLOW
capacity read also blocks lifetime efficiency; a valid unpolled capacity is usable.
This prevents mixing new inverter power with a failed/held meter reading. Valid
partial MPPT summation within one successful model retains its established meaning.

Equal decoded inputs reuse the previous **unrounded calculation**. Successful
new acquisitions still add an efficiency sample or report power. Identical HTTP
and storage status/availability pairs skip state writes. Source descriptors and
entity factories skip unchanged topology, avoiding repeated entity metadata work.
There is no second polling architecture, background sampler, extra Modbus request,
Recorder exclusion, global `force_update`, timestamp attribute or deadband.

## Publication resolution inventory

Rounding occurs only in sensor `native_value` / publication caches, using
`Decimal(str(value))` with `ROUND_HALF_UP`. Both positive and negative halfway
values round away from zero. Negative zero is published as zero. Scale factors,
calculation inputs, validation and write/readback values remain untouched.
Units, entity IDs, unique IDs, device classes and state classes are unchanged.

| Source / sensor type | Catalog registers or semantic keys | Published resolution |
| --- | --- | --- |
| 103 AC/DC voltage | PPVphAB/BC/CA, PhVphA/B/C, DCV | 0.1 V |
| 103 AC/DC current | A, AphA/B/C, DCA | 0.001 A |
| 103 active power | W, DCW | 0.1 W |
| 122 available active power | WAval | 0.1 W |
| 124 battery voltage | InBatV (when implemented by device) | 0.1 V |
| 160 MPPT and storage charge/discharge channels | module.DCV, module.DCA, module.DCW | 0.1 V / 0.001 A / 0.1 W |
| 203 meter phase/aggregate voltage | PhV, PhVphA/B/C, PPV, PhVphAB/BC/CA | 0.1 V |
| 203 meter phase/aggregate current | A, AphA/B/C | 0.001 A |
| 203 meter phase/aggregate active power | W, WphA/B/C | 0.1 W |
| Derived power | pv_power, grid_import_power, grid_export_power, consumption_power | 0.1 W |
| Informative efficiencies | inverter_efficiency, rectifier_efficiency, battery_lifetime_efficiency | 0.1 percentage points **after median** |
| Cumulative energy | 103 WH; 122 lifetime energy; 160 module.DCWH; 203 import/export counters | Original decoded precision |
| Ratings and settings | All 120/121/123, including WRtg, ARtg, MaxChaRte, MaxDisChaRte, VRef/VRefOfs/VMax/VMin/WMax; 124 WChaMax | Original precision (not measured instantaneous voltage/current/power) |
| Other quantities and controls | SoC, reserve/rates, HLC watt targets, VA/var, Hz, temperature, power factor, autarky, self_consumption, enums/flags | Original precision |

Disabled-by-default sensors use the same rules when enabled. SunSpec not-implemented
sentinels remain missing values; rounding does not manufacture measurements.
Meter import/export clamping and the existing battery charge/discharge classifier
run before publication rounding; no sign inversion or direction reassignment occurs.

## Median lifecycle

Each attached efficiency sensor keeps valid actual samples in a deque over the
closed interval `[now - 60, now]`, using monotonic coordinator acquisition time.
At FAST=5 s there are at most 13 regular samples per window, at FAST=2 s at most 31.
Explicit refreshes can add actual successful samples; they do not add SLOW deadlines.
Missing/invalid inputs, invalid modes, division by zero, NaN/Inf and failed model
reads add no samples. A callback on retained data does not refill the deque.

Odd counts use the central sample; even counts average the two central samples
with Decimal arithmetic. Only then is the median rounded. Publication occurs at
that unit's **normal SLOW deadline**, including the initial deadline, respecting
configured intervals and FAST quantization (for 7/30: 0,35,63,91,...). Readback or
recovery full reads between deadlines cannot publish efficiency early. A failed
or invalid due measurement holds the published value; the next normal SLOW
opportunity with a valid measurement can publish. An identical normalized median
needs no HA report because these sensors are informational.

There is no minimum-count/full-minute warm-up. One valid sample suffices at a
normal SLOW opportunity. With no initial valid sample the sensor is unavailable.
If initial recovery is between SLOW deadlines, sampling starts then and first
publication waits until the next normal SLOW opportunity. During long pauses the
window empties but the last valid published value remains. Resumption starts
with actual new samples; there is no interpolation, refill or catch-up burst.

A known Model 103 operating-state or AC-direction change clears the instantaneous
inverter/rectifier history, even if the new mode cannot produce an eligible
sample. Published values remain until a valid replacement median exists. A return
to the previous mode starts a fresh window, preventing old incompatible samples
from reappearing. Lifetime counters are mode-independent across charge/discharge;
a capacity change resets their sample window. Proven device/model/channel identity
changes reset both sample history and published derived values. Existing formula
eligibility (including MPPT state, same-unit sources and module uniqueness) remains.

## Read faults versus write authority

A timeout is not a measurement. The last successful model snapshot for the same
logical device is held **by object identity**, with no outage expiry. Successful
siblings continue to publish. Repeated FAST/SLOW timeouts and failed second chunks
do not cause sensor `unavailable`, zero, Recorder changes or new `last_reported`.
An initial failure cannot create a valid snapshot. Successful recovery reports
immediately for raw/derived power, even if the normalized value is unchanged;
efficiencies retain their SLOW publication rule.

A transport failure while discovering does not prove removal. A successfully read
invalid SunSpec signature/chain, a successfully discovered changed or empty chain,
or changed Common manufacturer/model/serial or Model 160 channel identities does
invalidate incompatible readings. Firmware version alone is not identity. Old
SLOW snapshots are not carried into an identified replacement. A decode failure
is not a transport failure: it remains invalid rather than silently borrowing a
previous raw model. Derived invalid calculations retain the last complete result.

The memory-only publication view never populates `live_models`, changes a TCP
generation, extends a discovery deadline or renews a remote-control lease. Failed
live models/devices remain failed, and existing session/discovery validation,
preflight, write policy, readback and watchdog behavior remain unchanged. Read-only
storage status displays its last confirmed classification during a read outage;
its internal classifier still returns unknown and controls cannot use this display
as confirmation. Persisted topology is still structural and carries no readings.

The device can be physically different from its held value. With no successful
identity observation an identical-layout replacement cannot be proven; there is
no estimator or invented correction. Hold-last is deliberately session-local:
unload/restart discards measurements; persisted topology alone never restores them.
Optional Solar API HTTP statuses retain their existing failure semantics.

## Home Assistant event and timestamp semantics

- `last_changed`: HA state text changed.
- `last_updated`: HA state text or attributes changed.
- `last_reported`: the integration reported state, including an identical state.

An identical successful normalized FAST reading advances `last_reported`, while
`last_changed` and `last_updated` stay fixed. HA emits `state_changed` for a changed
state/attributes and `state_reported` for an unchanged report; these event counts
are not identical to total state writes. Entity-filtered `state_reported` listeners
are required by HA. Failed reads do not invoke the sensor's state-write callback.
Initial entity creation and unrelated platform writes can also report state, so
`last_reported` is HA reporting time, not proven physical acquisition time.

The regression tests use the installed HA state machine and event bus. Recorder
rows below are **expected state rows inferred from changes**, not measured database
inserts. Normal identical reports without `force_update` add no state-change row.
Attribute-only changes, Recorder configuration, startup/restores and other HA
activity can affect actual rows. Long-term statistics are not evaluated here.

## Reproducible before/after assessment

Run from the repository root in the development Python environment:

```sh
python -m tools.benchmark_measurement_publication
python -m pytest -q tests/test_measurement_publication.py tests/test_polling.py
ruff check .
python -m pytest -q
git diff --check
```

The synthetic one-hour scenario has 720 successful FAST samples (5 s), 120 SLOW
opportunities (30 s), constant attributes and repeating +/-0.02 jitter around
1000 W and 96%. Initialization is included as the first state change. The reference
is v1.2.0's existing selective FAST/SLOW polling, not an all-model FAST poller.

| Per-hour synthetic result | Before | After |
| --- | ---: | ---: |
| Raw power HA state writes | 720 | 720 |
| Raw power state_changed | 720 | 1 |
| Raw power unchanged state_reported | 0 | 719 |
| Efficiency HA state writes / state_changed | 720 / 720 | 1 / 1 |
| Efficiency normal publication opportunities | 720 FAST callbacks | 120 SLOW deadlines |
| Expected Recorder state rows, those two sensors | 1440 | 2 |
| Example topology payload requests | 4320 | 4320 |
| Example topology payload registers | 222720 | 222720 |

These counts illustrate a stable signal with small jitter, not general savings.
If power crosses a normalization boundary every FAST poll, up to 720 changes/hour
remain. A changing efficiency can publish up to 120 changes/hour at default SLOW.
Stable high-level status/availability suppresses repeated writes (one initial write
instead of 720 in this idealized case). Energy precision/reporting is unchanged.

The documented two-unit topology retains 5 requests / 267 registers per FAST-only
cycle plus 6 / 254 at SLOW. Formula: requests/hour = `5*720 + 6*120 = 4320`, registers
= `267*720 + 254*120 = 222720`. Discovery adds approximately 168 requests/hour in
steady healthy operation (14 per 300 seconds), exactly as before. Startup, faults,
revalidation, explicit readbacks and write traffic can add reads. No additional
Modbus traffic is introduced by median calculation or publication normalization.

A local **median-only synthetic microbenchmark**, Python 3.14.7, 100 repetitions of
three windows over 720 samples each, observed about **0.014 seconds per simulated
hour**, with **4304 retained / 6900 peak traced bytes** for three populated windows
(13 samples each). Timing varies by host/run. It measures library work, not live
HA CPU, total integration memory, database size or actual savings. The script's
`perf_counter` measures elapsed runtime; it is not process CPU accounting.

Median insertion/expiry is amortized O(1); publication sorts O(n log n) samples.
Memory is O(number of samples in 60 s). The separate publication view shares model
objects with live data and holds old model payloads only where needed; formula
caches hold a previous complete source set. Those payloads, entity overhead,
Decimal power normalization, topology checks and HA dispatch are excluded from the
median-only numbers. Live CPU/Recorder/I/O measurements are still required for
real deployment claims.

## Verification and residual limits

Final verification: **1424 passed**, with one existing aiohttp/HA deprecation
warning (`HomeAssistantApplication` inheritance). Ruff and `git diff --check` pass.

The complete suite covers existing safety, storage direction/quantization,
remote control and low-level writes. Publication regressions cover signed decimal
halfway cases, same/crossing quantization boundaries, real HA events, exact energy,
odd/even/outlier medians, expiry, configured 5/30,7/30,5/60 deadlines, invalid inputs,
mode changes, no held-sample refill, isolated/serial read faults, failed SLOW reads,
second chunk failure, startup, changed/empty/invalid topology, serial replacement,
shared endpoint generation revalidation, unchanged/changed recovery and retained
control status without write permission. Fake storage writes demonstrate identical
control plans for absent, low, high and excessive informative efficiency values.

All fault injections and writes in tests use synthetic transports. No running
Home Assistant integration, physical Modbus write, release version, tag, merge or
release is part of this change. Stable manifest version stays v1.2.0. Physical
measurement cadence and real CPU/database savings remain unmeasured. Held values
can be stale indefinitely by design; an unavailable writable control and an
available held sensor are intentional independent states.


## Changed files

| Area | Files |
| --- | --- |
| Live/retained data separation | `custom_components/fronius_pv_manager/coordinator.py`, `model_entity.py` |
| Precision, derived publication, median | `custom_components/fronius_pv_manager/measurement.py` (new), `measurement_entity.py` (new), `sensor.py` |
| Status and entity construction | `custom_components/fronius_pv_manager/solar_entity.py`, `storage_status.py` |
| Documentation | `README.md`, `docs/polling.md`, `docs/measurement-publication.md` (new) |
| Publication regressions | `tests/test_measurement_publication.py` (new) |
| Existing regression expectations | `tests/test_efficiency_sensors.py`, `test_rectifier_efficiency.py`, `test_offline_runtime.py`, `test_polling.py`, `test_sensor.py` |
| Reproducible assessment | `tools/benchmark_measurement_publication.py` (new) |
