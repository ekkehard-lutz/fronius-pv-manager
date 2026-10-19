# Configurable FAST/SLOW polling

## Runtime and settings

One `FroniusPVCoordinator`, one endpoint client and its existing cancellation-safe
I/O lock own discovery, reads and writes. No second coordinator or independent
polling task is created. The Home Assistant coordinator interval is FAST.
Each refresh reads the complete FAST model payloads in discovery order; SLOW
payloads are read only when due. Large payloads retain the existing sequential
100-register chunking. Unknown models remain in discovery but are not decoded.

Integration **Configure** options, with English and German labels/help:

| Option | Default | Inclusive range |
| --- | ---: | ---: |
| Fast polling interval (seconds) | 5 | 2–30 |
| Slow polling interval (seconds) | 30 | 10–300 |

SLOW must be greater than or equal to FAST. Both integers are persisted in config
entry options. Missing options use 5/30 automatically, including existing entries.
The options update listener reloads the integration only when the effective
intervals change; topology cache updates cannot cause reload loops. Unload drains
I/O, cancels the coordinator timer/debouncer and closes the old client before
setup creates the new runtime. Entity and device unique IDs are unchanged.

At startup, recovery, session change and discovery renewal, all supported models
are read. An explicitly requested refresh (including verified writes) also reads
all supported models so SLOW control/settings readback is not delayed.

With healthy, negligible-duration reads, default cycles are full at t=0, FAST
at t=5/10/15/20/25, full at t=30. Each unit has a monotonic SLOW deadline anchored
to its initial poll. The first FAST refresh at or after that deadline services
SLOW and advances the deadline by whole elapsed SLOW periods. For 7/30, nominal
full cycles are t=0,35,63,91,126, rather than t=0,35,70,105. Missed cycles are
skipped, not replayed in a burst. FAST models are never read twice in one cycle.
SLOW can be delayed by less than one FAST interval in healthy operation; this is
explicit quantization to the single coordinator cadence, not an exact-deadline
second timer.

Intervals are scheduling targets, not realtime guarantees: HA scheduling, serial
Modbus I/O, discovery, writes and the optional Solar API affect wall-clock timing.
The coordinator uses HA's normal completion-based refresh scheduling; FAST can
take longer than configured when I/O is slow. SLOW deadlines do not accumulate
that duration or rounding drift. No catch-up requests or aggressive concurrency
are introduced.

The existing optional Solar API request remains once per coordinator refresh,
now normally once per FAST interval, with its existing three-second timeout. It
is **HTTP**, excluded from the Modbus counts below. It still precedes publication
of the coherent snapshot, so an unavailable HTTP endpoint can delay publication
by up to its timeout. Hardware testing must measure actual end-to-end cadence.

## Whole-model classification

Register definitions are Python files in `register_maps/`, not YAML. The YAML
file is the write policy. Existing per-register `PollClass` metadata is not a
request schedule: whole payloads keep their scale factors, counters and readings
together. Payload coordinates are discovered at runtime, never hard-coded.

| Cadence | Model | Payload length in example | Reason |
| --- | --- | ---: | --- |
| FAST | 103 | 50 | Inverter active power `W`, phase currents/voltages, aggregate DC and operating state; inputs to site load and efficiency. |
| FAST | 124 | 24 | Battery `ChaState` (SoC), charge status, voltage and storage controls together in a small block. |
| FAST | 160 | 88 | Four 20-register DC channel blocks plus 8 fixed registers: MPPT and battery charge/discharge `DCW`, `DCA`, `DCV`, channel identities and scale factors. PV power is derived from classified MPPT channels. |
| FAST | 203 | 105 | Grid total and phase power, phase currents/voltages and scale factors. Two sequential reads (100+5); included energy counters do not justify splitting this block. |
| SLOW | 1, each unit | 65 | Common identity, manufacturer, firmware and serial metadata. |
| SLOW | 120 | 26 | Nameplate ratings/capabilities; also capacity input to lifetime efficiency. |
| SLOW | 121 | 30 | Basic configuration/settings. |
| SLOW | 122 | 44 | Extended status, available power and lifetime counters; no input to current PV/grid/load power calculations. Status can lag by SLOW. |
| SLOW | 123 | 24 | Immediate-control configuration/setpoints, refreshed also after writes. |

FAST reads **all offsets 0 through discovered length minus one**, including
co-located static values and counters. Model 160 length depends on channel count;
the decoder and existing repeating-block validation remain unchanged. Future
supported models default to SLOW until explicitly classified. Unsupported
floating-point models are not newly supported by this feature.

## Failure, recovery and publication

Successful FAST reads publish immediately after the coordinator refresh completes.
Unpolled SLOW snapshots retain exact object identity, suppressing redundant writes.
Raw sensor publication uses `measurement_data`: failed **reads** reuse the last
valid model snapshot for that device, so no callback publication or freshness is
invented. A failed second chunk never commits a partial model. Repeated timeouts,
including discovery transport failures and TCP resets, have no age-based expiry.
Initial missing data remains unknown/unavailable. A decode error remains an invalid
model, rather than being treated as a successful read or silently masked.

Live `data` still contains failed/empty models and unavailable devices. Discovery
expires after 300 seconds and session generation changes invalidate authority.
Failures clear that unit's discovery authority and normal polling attempts recovery.
Revalidated identical topology permits continuation of the sensor series; proven
invalid discovery, changed model coordinates or changed Common identity/channel
identities clear incompatible readings and derived histories. Cached SLOW data from
the old identity cannot fill the replacement's missing measurements. Units are
isolated even when sharing a TCP endpoint. No held measurement changes write
validation, readback, ownership, lease/watchdog behavior or live control snapshots.
Read-only control status retains its displayed classification; the internal
classifier and writable controls continue to require confirmed live state.

`ModelCoordinatorEntity` retains its source-identity/availability suppression.
Numbers/selects still resolve live data. Raw sensors override the source view to
retained measurement data. Successful reads create new model objects even for
identical values, advancing `last_reported`; failed reads preserve object identity.
Semantic power/consumption calculations require complete successful FAST inputs
from the same refresh and otherwise retain the last complete result. Equal input
payloads reuse their raw calculation, while a successful new measurement still
reports power. HTTP statuses and control status suppress identical state writes;
source descriptors/entities are not rebuilt for unchanged topology.

The three informative efficiencies sample the existing successful FAST payloads,
keep valid samples in `[now - 60, now]`, and publish only at **normal SLOW deadlines**.
Forced full readbacks and rediscovery do not create additional publication deadlines.
A failed/invalid due measurement does not publish even a pending historical median.
The next normal SLOW opportunity with a valid sample can publish. Equal medians do
not need repeat reports. There is no separate Modbus access, task or timer. Lifetime
efficiency may reuse valid unpolled SLOW capacity, but never a failed attempted read.

Decimal `ROUND_HALF_UP` normalizes voltage/current/active power at the HA boundary;
cumulative counters and control inputs retain original precision. See the complete
[sensor inventory, median lifecycle and reproducible assessment](measurement-publication.md).
No public timestamp attributes/entities, `force_update`, hysteresis or Recorder
configuration changes are introduced.

Home Assistant state timestamps have different meanings:

- `last_changed`: the state value changed.
- `last_updated`: the value or attributes changed. An identical successful FAST
  sample does **not** advance this timestamp.
- `last_reported`: the integration wrote state, even with identical value and
  attributes. Normal successful FAST reports advance this timestamp; retained
  raw SLOW model states are not rewritten on FAST-only refreshes.

Consumers that accept identical measurements should use `last_reported` together
with availability, rather than treating old `last_updated` as proof of an outage.
This timestamp describes an HA report, not the device's physical measurement
acquisition time; initial entity creation or other HA state writes can also report
state. `state_changed` listeners alone do not receive unchanged reports. Consumers
can inspect state at their control cadence or use properly entity-filtered HA
`state_reported` listeners. No Wallbox Manager changes are included.

References: [HA timestamp semantics](https://developers.home-assistant.io/blog/2024/03/20/state_reported_timestamp/)
and [HA state changes](https://data.home-assistant.io/docs/states/).
The tests exercise the installed HA state machine as well as the integration's
selective state-write callback. `last_updated` alone is insufficient; using the
existing `last_reported` field is the smallest clean consumer-side solution.

## Communication and diagnostics

The [official GEN24 Modbus manual](https://manuals.fronius.com/html/4204102649/en-US.html)
recommends sequential requests (at most two parallel), a timeout of at least one
second, avoiding millisecond request intervals, and contiguous reads. The shared
lock and client remain unchanged: only one integration Modbus request at a time,
including discovery and verified write sequences. Pinned pymodbus 3.13.1 defaults
to a three-second timeout; retries remain zero. Other TCP clients are outside this
integration's lock. There is no asserted fixed request-per-second limit or lockout
threshold for GEN24.

Set **Diagnostic level 3** in Configure for bounded technical summaries, or level 1
for communication/recovery only. The diagnostic level is read live; it does not
change poll deadlines or reload the runtime. Standard Python WARNING visibility
suffices; no global DEBUG setting is needed. The [diagnostic event inventory](diagnostics.md)
describes the structured `FPMGR subsystem=... {JSON}` format and deduplication.

One `poll_summary` per unit per minute reports FAST/FAST+SLOW, selected model/base/
length groups, planned payload reads/registers, discovery flag, elapsed milliseconds,
session generation and result. `planned_reads` / `planned_registers` equal actual
payload calls/registers only on successful cycles. Failures can stop chunking early;
counts exclude discovery, write/readback traffic, reconnect handshakes and HTTP.
Elapsed time covers that unit's discovery and payload work, excluding other units,
lock wait and optional HTTP. These are diagnostic scheduling summaries, not measured
wire packets or physical acquisition timestamps. No sensor attributes are added.

Level 1 reports an error-triggered rediscovery after a read/session failure, with
one recovery event when validated live discovery and complete reads resume. A normal
300-second discovery renewal remains a healthy operation (detail only at level 3).
Held sensor availability never determines communication recovery or write authority.

## Reproducible Modbus cost

Example: inverter unit exposes 1/103/120/121/122/123/124/160 with lengths
65/50/26/30/44/24/24/88; meter unit exposes 1/203 with lengths 65/105.
This is a synthetic reproduction of the documented model set, not newly measured
hardware evidence. Unknown extra models increase discovery cost.

| Work | Requests | Registers |
| --- | ---: | ---: |
| FAST-only | 5 | 267 |
| Additional SLOW payloads | 6 | 254 |
| FAST+SLOW | 11 | 521 |
| Both-unit discovery | 14 | 28 |

Discovery reads a two-register signature and one two-register header per model,
including the terminating header: 10 reads on the inverter, 4 on the meter.
Payload reads split only Model 203 into 100+5. Discovery is approximately once per
300 seconds in healthy operation, plus startup/recovery as above.

Steady-state nominal costs (ignoring startup, faults, writes and scheduling delays):

| Scheme | Payload req/s | Payload req/min | Payload registers/min | Req/s incl. discovery | Req/min incl. discovery | Registers/min incl. discovery |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A: complete every 30 s (previous) | 0.3667 | 22 | 1,042 | 0.4133 | 24.8 | 1,047.6 |
| B: selective 5/30 s | 1.2000 | 72 | 3,712 | 1.2467 | 74.8 | 3,717.6 |
| C: complete every 5 s | 2.2000 | 132 | 6,252 | 2.2467 | 134.8 | 6,257.6 |
| D: complete every 1 s (hypothetical) | 11.0000 | 660 | 31,260 | 11.0467 | 662.8 | 31,265.6 |

B = `5/5 + 6/30` requests/s; registers/min = `267*12 + 254*2`.
Discovery adds `14/300` requests/s and `28/5` registers/min. B reduces payload
requests by 45.5% and registers by 40.6% compared with C, while providing six times
the nominal dynamic read frequency of A. These are average loads, not device
capacity limits. Actual cycles depend on topology and response time.

## Live beta verification

1. Record GEN24/Smart Meter/battery firmware, discovered units/models/lengths and
   baseline timeout/error counts. Back up HA configuration; install the reviewed
   feature build without changing writable policy or control targets.
2. Leave interval options absent initially. Confirm 5/30 defaults. Set diagnostic
   level 3 temporarily and capture at least ten minutes, including discovery renewal.
3. Inspect per-unit summaries and a request trace: healthy summaries are rate-limited
   to one per minute, so they do not enumerate every cycle. A read-only trace should
   show approximately five FAST-only cycles between full cycles and example payload
   counts 5/267 versus 11/521 summed over units; discovery remains about every 300 s.
4. Inspect representative inverter power, meter phase current, PV/grid/load power
   and battery SoC states. Record value, availability, `last_reported`,
   `last_updated`, `last_changed`. Stable FAST readings should still be reported;
   raw SLOW sensor `last_reported` should remain unchanged between actual reads.
   Measure end-to-end interval including optional HTTP latency.
5. Change options to 7/30, then 5/60, then restore 5/30. Confirm one clean reload per
   change, unchanged entity IDs, FAST cadence change and deadline-based SLOW reads.
   Try 20/10 and out-of-range inputs and verify rejection without runtime changes.
6. Compare timeouts/errors and availability against the baseline. Check inverter,
   meter and battery separately and verify PV/grid/load signs and arithmetic under
   changing production, household load and battery charging/discharging.
7. During a safe maintenance window, interrupt connectivity and restore it. Verify
   held valid sensor states with unchanged `last_reported`, independent unit
   recovery, full recovery reads, unchanged identities, unavailable writable
   controls and no old discovery reused as write authority.
8. Exercise one already approved, reversible writable control using the existing
   safe test procedure. Verify serialized write/readback, confirmed entity state,
   no duplicate write and normal polling afterward; restore the original setting.
   Restore diagnostic level 0 when finished.

## Separate temporary physical-cadence experiment

A five-second read does not prove a new physical sample every five seconds.
Internal GEN24, BMS and meter-through-GEN24 publication rates remain unverified;
shorter configured intervals cannot force devices to generate measurements faster.

In a separate maintenance experiment, pause this integration and other pollers,
then use a temporary read-only script against **selected contiguous raw blocks**
for inverter `W`, meter `W`/phase currents and, separately, relevant storage/DC
channels. Reuse existing discovery, transport and maps; resolve scale factors
without per-register chatter. Log monotonic request/response times, raw words,
scaled values and errors approximately once per second for 60–120 seconds, with
one outstanding request, at least a one-second timeout and no catch-up bursts.
Stop on errors or rising latency. Apply a safe observable load change and correlate
raw value change times with it. Repeated identical numbers during steady load do
not establish publication cadence. Repeat conditions and document firmware,
models, load and latency before inferring anything. Close the diagnostic client
and restore normal integration polling. No such diagnostic poller or one-second
production option is implemented here; the production minimum remains two seconds.
