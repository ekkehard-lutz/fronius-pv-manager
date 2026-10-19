# Four-level diagnostics and communication recovery

## Configuration and runtime boundary

Baseline: feature/measurement-recorder-optimization at
`7067d25d1be215e36b9101bb4a49195d13b2237c` (measurement-publication change).
The existing FAST/SLOW coordinator, endpoint transport, serialized I/O lock,
timeouts, retries, request ordering, cancellation and control semantics remain.

Configure the integration entry and select `diagnostic_level`, an integer 0–3,
default 0. English and German field descriptions explain every level. Invalid
booleans, strings, fractional numbers and out-of-range values are rejected by the
options flow. Malformed legacy stored diagnostic values select 0 defensively.
Partial edits preserve unrelated options and the effective FAST/SLOW defaults.

| Level | Selection | Python severity for new diagnostic events |
| --- | --- | --- |
| 0 | Unexpected runtime, lifecycle, protocol or unrecoverable control errors | ERROR |
| 1 | Also initial TCP connection, transport communication and validated recovery | WARNING |
| 2 | Also actual live transitions, explicit control results, remote-lease lifecycle | WARNING |
| 3 | Also bounded model/discovery/polling/preflight/readback detail | WARNING |

The level selects events; Python severity controls visibility. HA's standard
WARNING filter shows levels 1–3 when opted in. Explicit logger configuration
stricter than WARNING can still hide them. No global logger level is changed.
Existing critical configuration ERROR output remains functional; HA's own coordinator
and legacy defensive configuration-validation logs retain their native behavior.

`Diagnostics` is owned by the coordinator, with an options getter that reads the
entry's current options for each event. The endpoint shares that same emitter.
Changing only the diagnostic level does **not** reload or restart anything, close
TCP, rediscover, reschedule polling, write Modbus, clear measurement caches/median
windows or touch leases/watchdogs. FAST/SLOW changes keep the established reload.
There is no diagnostic timer, task, entity, Recorder entry or timestamp attribute.

The concept follows Wallbox Manager's
[`docs/v0.4/recorder-availability-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/b472db435737d766e566fb9d843c92e59c507466/docs/v0.4/recorder-availability-report.md):
a small owner-local emitter, level selection separate from WARNING visibility,
structured fields, bounded event families and failure-isolated logging. Fronius
adds endpoint-versus-device episode scopes for shared Modbus TCP sessions.

## Format and event inventory

Every new diagnostic message is `FPMGR subsystem=<name> {JSON}`. JSON keys are
sorted. Each message includes `event` and `entry_id`, plus meaningful fields such
as `reason`, `device_id`, `model_id`, `connection_generation`, `discovery_revision`,
`result`, `elapsed_ms`, `planned_reads` and `planned_registers`. State messages use
known decoded operating-state labels, not inferred power or translated UI text.
Passwords, tokens, owner identifiers, control values, complete raw payloads,
upstream exception text and upstream frame dumps are not logged by this emitter.

| Event | Minimum level | Severity | Trigger | Deduplication |
| --- | ---: | --- | --- | --- |
| `runtime_error` | 0 | ERROR | Unexpected exception from coordinator work; original exception still propagates | Actual exception |
| `lifecycle_error` | 0 | ERROR | Setup/lifecycle failure or failed transport cleanup | Actual failure |
| `protocol_error` | 0 | ERROR | Structural decode defect or response transaction/device identity violation | Per model 60 s; endpoint identity violations 300 s |
| `upstream_error` | 0 | ERROR | Unexpected pymodbus ERROR during this entry's client call | Per upstream function, 60 s; sanitized |
| `control_failed` (unexpected) | 0 | ERROR | Unexpected exception from a control operation, including persistence failure | Actual operation; re-raised |
| `lease_fallback_failed` | 0 | ERROR | Expiry fallback failed, cleanup remains pending | Existing one-shot watchdog failure |
| `communication_availability_timeout` | 1 | WARNING | Continuous per-unit communication failure reaches configured timeout; includes `device_id`, `timeout_seconds`, `result="unavailable"` | Once per actual timeout transition |
| `communication_availability_recovered` | 1 | WARNING | A timed-out unit supplies a valid Modbus response; includes `device_id`, `timeout_seconds`, `result="responding"` | Once per communication transition; entity sources may still be invalid |
| `communication_failure` | 1 | WARNING | Register read/write/response or poll communication failure | Per device and reason, 300 s |
| `connection_failure` | 1 | WARNING | Actual TCP connection attempt failed | Endpoint and reason, 300 s |
| `connection_lost` | 1 | WARNING | Connected transport observes closed underlying client | Endpoint and reason, 300 s |
| `session_reset` | 1 | WARNING | Transport actually resets/discards its client session | Endpoint and reset reason, 300 s |
| `connection_attempt` | 1 | WARNING | Actual connection attempt during an endpoint outage | Endpoint, 300 s |
| `connection_established` | 1 | WARNING | Initial TCP connection or a later reconnection genuinely succeeds; `reason="initial"` or `reason="reconnect"` | Once per successful physical TCP session, shared across device IDs; no time-based suppression |
| `rediscovery_started` | 1 | WARNING | Discovery starts because of a device/shared-endpoint failure episode | Device and recovery reason, 300 s |
| `discovery_failed` | 1 | WARNING | Discovery transport failure or invalid SunSpec chain | Device and reason, 300 s |
| `discovery_recovered` | 1 | WARNING | Error-triggered discovery returns a validated model chain | Device, once per episode/300 s if payloads keep failing |
| `communication_recovered` | 1 | WARNING | Complete successful device payloads with current-session discovery | Once per device episode |
| `endpoint_recovered` | 1 | WARNING | Every configured unit sharing that endpoint has valid discovery and successful payloads | Once per endpoint episode |
| `device_availability_changed` | 2 | WARNING | Live device snapshot availability changes | Transition only |
| `operating_state_changed` | 2 | WARNING | Successful Model 103 St or Model 124 ChaSt observation changes | Transition only; never held data |
| `topology_changed` | 2 | WARNING | Validated discovered model coordinates/capabilities change | Transition only |
| `capabilities_changed` | 2 | WARNING | Successfully decoded Model 160 channel identities change | Transition only |
| `write_authority_changed` | 2 | WARNING | Observed live session/discovery authority changes or becomes invalid | Transition only; includes revision |
| `control_requested` / `control_completed` | 2 | WARNING | Explicit HLC/LLC operation starts/completes; LLC completion follows verified readback and refresh | Actual operation; idle settings say `completed`, physical LLC result says `verified` |
| `control_failed` (expected/rejected) | 2 | WARNING | Existing validation/ownership/write checks reject or fail an operation | Actual operation; error type, no input values |
| `lease_acquired` / `lease_released` | 2 | WARNING | Verified remote acquisition / completed automatic release | Actual lifecycle event |
| `lease_expired` / `lease_expiry_completed` | 2 | WARNING | Expiry cleanup starts / verified automatic fallback completes | Actual lifecycle event |
| `lease_renewed` | 3 | WARNING | Existing heartbeat or successful command renews a live lease | Per device, 60 s |
| `poll_summary` | 3 | WARNING | Existing FAST/FAST+SLOW poll completes | Per device, 60 s |
| `discovery_started` / `discovery_completed` | 3 | WARNING | Existing initial/scheduled/session/recovery discovery starts/validates | Per device/event, 60 s |
| `model_read_failed` | 3 | WARNING | Existing complete-model read/decode fails | Per device/model, 60 s |
| `write_preflight_completed` / `write_readback_completed` | 3 | WARNING | Existing plans prepared / writes semantically read back | Actual operation; model/register or aggregate step count |

An initial live transition observation establishes a silent baseline. Unchanged
states do not repeat. Duplicate required model IDs are ambiguous and cannot
produce physical operating-state transitions. Authority observation includes its generation, revision and
validated model tuple; normal renewal can legitimately change the authority
revision at level 2 without implying a communication failure at level 1.

## Failure episodes and recovery

A first timeout logs `communication_failure` and the transport's actual
`session_reset`. The next existing scheduled refresh may then log
`rediscovery_started`, `connection_attempt`, and a genuinely successful
`connection_established`. A still-failed discovery logs `discovery_failed`.
No extra connection attempt, read, write or rediscovery is initiated by logging.

After connection succeeds, a validated SunSpec chain logs `discovery_recovered`.
This does not claim measurement recovery: a subsequently failed model read still
blocks `communication_recovered` and write authority through the established
failure path. Full successful device reads with live discovery end the device
episode. The endpoint episode ends only when **all** its configured device IDs
have valid current discovery and complete successful payloads. A healthy sibling
cannot hide a failing unit or repeatedly restart endpoint recovery.

The emitter remembers event/reason pairs per device or endpoint. Identical pairs
are suppressed for 300 monotonic seconds; the next occurrence after the deadline
contains the number `suppressed`. Materially different classifications (timeout,
connection loss/failure, error response, invalid response, incomplete response,
invalid SunSpec chain) are visible immediately. At most 32 signatures are retained
per episode; excess unusual signatures share a bounded overflow bucket. Successful
recovery clears episode suppression, so a later independent outage warns again.
No timer performs summaries: a summary needs a subsequent real occurrence.

A physical endpoint deduplicates reset/failure/attempt warnings across units.
Successful connections instead emit `connection_established` exactly once per
physical TCP session, even if several device IDs (such as 1 and 200) share it.
Device read/discovery failures include their own device ID. Fake or other transports that do not report an actual reset do not produce reset warnings.
Ordinary close/unload is not reported as a communication failure.

The first successful physical TCP connection logs `connection_established` at
level 1 with `reason="initial"`, `result="connected"`, `entry_id` and the current
`connection_generation`. Failed initial attempts do not count as a successful
connection; the eventual first success still has `reason="initial"`. Each later
successful physical session has `reason="reconnect"`. Generations retain their
existing semantics: failures and session resets can also advance them, so they
are not simply a successful-connection counter. Failed connection attempts never
emit this success event. Successful sessions bypass the 300-second suppression
window, so a genuine reconnection soon after startup or another reconnect remains
visible. Repeated `connect()` calls while connected produce no extra event.

Level 0 remains silent for successful connections. Successful-session history is
tracked independently of the selected diagnostic level: enabling level 1 on an
already connected endpoint neither replays nor invents an initial connection,
and its next genuine reconnect has `reason="reconnect"`. No reload is needed.
This event confirms TCP establishment only; it does not confirm SunSpec discovery,
valid measurements, communication recovery or recovered write authority. Those
remain separate validated events.

Initial SunSpec discovery, approximately 300-second scheduled renewal and healthy
FAST/SLOW polling produce no additional level 1 connection messages. Level 3
summaries are not a per-cycle packet capture: they show the selected cycle and planned request/register
counts, with actual elapsed duration, at most once per minute per unit. Errors can
stop chunking early. No unchanged register values or payloads are dumped. SLOW
polling and informative median publication remain on their previous deadlines.

## Native pymodbus logging isolation

Pinned pymodbus 3.13.1 logs receive timeouts/connect failures at ERROR itself, and
can append raw recent frames. Leaving those records untouched would bypass level 0
and episode suppression. A single dormant filter on `pymodbus.logging` therefore
normalizes records **only inside this integration's synchronous client calls**.
A ContextVar scopes it to the current call/thread/task; the context is restored
in `finally`. No global logger level is modified, and records outside owned calls
(including other integrations) pass unchanged. The filter holds no entry reference.

Expected upstream connection/timeout output is replaced by the transport's
structured events. Other upstream ERROR records become bounded sanitized
`upstream_error` ERROR events; DEBUG/INFO/WARNING and raw frame detail inside owned
calls are not copied. The pinned library's known no-response exception marker maps
to `timeout`; device/transaction identity mismatch maps to `protocol_error`.
Unknown upstream exception text is never emitted. A pymodbus upgrade must review
these markers and logger boundaries. Standalone tools without the runtime emitter
retain native pymodbus logging.

## Retained values and actual communication

The emitter observes transport operations and live coordinator snapshots, never
sensor availability as a health oracle. A last-valid sensor remains available and unchanged before its per-device
communication timeout, while failure warnings and loss of live write authority
are visible. At expiry it transitions to unavailable once; repeated failed polls
do not report it again. The cached value remains internal. No failed read creates
a fresh measurement, efficiency sample, measurement correction or lease renewal.
The two availability events are separate from full `communication_recovered` and
`endpoint_recovered`: a valid discovery response or read chunk proves a responding
unit but does not validate every model or restore write authority.
See [communication availability](communication-availability.md). Initial invalid values and proven identity/topology incompatibilities
retain the previous protections, including an incompatible/empty discovery chain.

Control diagnostics surround existing operations, without changing their I/O lock,
preflight, readback, validation, persistence or cancellation behavior. Heartbeats
produce no periodic level 2 operation messages; successful renewals have bounded
level 3 detail, and rejected heartbeats can report a level 2 failure. Expiry reports
a completed fallback only after the existing cleanup succeeds. Logging/JSON/options
collection failures are isolated and cannot reject a poll or control operation.

Example messages (illustrative; timestamps are provided by Python/HA, not sensor
attributes):

```text
FPMGR subsystem=modbus {"connection_generation": 1, "entry_id": "example", "event": "connection_established", "reason": "initial", "result": "connected"}
FPMGR subsystem=modbus {"connection_generation": 1, "device_id": 1, "entry_id": "example", "event": "communication_failure", "reason": "timeout", "suppressed": 0}
FPMGR subsystem=modbus {"connection_generation": 2, "entry_id": "example", "event": "session_reset", "reason": "timeout", "suppressed": 0}
FPMGR subsystem=sunspec {"device_id": 1, "entry_id": "example", "event": "rediscovery_started", "reason": "communication_recovery", "suppressed": 0}
FPMGR subsystem=modbus {"connection_generation": 3, "entry_id": "example", "event": "connection_established", "reason": "reconnect", "result": "connected"}
FPMGR subsystem=modbus {"connection_generation": 3, "device_id": 1, "discovery_revision": 2, "entry_id": "example", "event": "communication_recovered", "result": "recovered"}
```

## Validation and limitations

Tests use deterministic clocks, synthetic clients and real local HA state objects.
They cover 0–3 selection/schema/translations, partial option persistence, live
changes during active worker I/O, retained measurements, genuine remote leases and
median history; timeout/response/chunk/connection/discovery failure and recovery;
episode suppression/material changes/independent outages/shared endpoint scopes;
normal scheduled renewal and bounded detailed summaries; equal polling traces at
all levels; expiry authority rejection; operating/topology/channel transitions;
control rejections; real pymodbus exception classification; foreign logger
isolation, sanitized upstream errors and broken logger/options/JSON collection.
The complete existing measurement, polling, recovery, storage/remote control and
low-level-write suites are included. All test writes are fake; no physical writes,
Home Assistant deployment, version bump, tag, release or merge is performed.

Original four-level diagnostics result: **1475 tests passed**, including all 1424
baseline tests and 51 new parameterized diagnostic cases. One existing upstream aiohttp/Home Assistant
`HomeAssistantApplication` inheritance DeprecationWarning remains. Ruff lint,
formatting of all 12 changed Python files and `git diff --check` pass. The repository-wide
format check already reports **45 pre-existing files** at the required baseline;
the same 45 remain, with **no new format violations**. They are left unchanged to
avoid unrelated formatting. Compare the baseline in a temporary `git archive`
checkout, using the same installed Ruff, rather than accepting a new formatting
failure as pre-existing. 

Changed files: `README.md`, `docs/polling.md`, this event inventory;
`custom_components/fronius_pv_manager/{__init__,const,config_flow,coordinator,transport,
storage_control,write_runtime,diagnostic_logging}.py`; `strings.json` and German/
English translations; `tests/test_polling.py`, `test_polling_options.py`,
`test_storage_remote.py` and new `test_diagnostic_logging.py`.

The initial TCP connection follow-up adds 22 parameterized regression cases:
HA entry startup at levels 0–3 with single/shared endpoints, repeated connects,
healthy polling/scheduled discovery, failed initial and later attempts (false
return or exception), multiple successful reconnects within 300 seconds,
reconnects while discovery still fails, unchanged recovery events/generations,
and live opt-in without replay. The complete follow-up suite passes **1497 tests**,
with the same upstream deprecation warning; repository-wide Ruff lint, formatting
checks for the two changed Python files and `git diff --check` pass.

Hardware validation remains outstanding: verify timeout/reconnect classifications
and practical log volume on actual GEN24/meter firmware with multiple unit IDs.
This change does not establish physical measurement cadence or live CPU/Recorder
savings. Recovery means successful Modbus communication and validated discovery,
not a guarantee about unobservable physical values held through an outage. HA may
filter logger output externally; dropped logs/failed log handlers do not prove a
healthy device. Baselines established while level 2 is disabled are not replayed
as historical physical transitions when enabling it.
