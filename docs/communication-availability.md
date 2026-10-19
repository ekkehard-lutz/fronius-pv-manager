# Per-device communication availability

## Configuration

Configure **Communication availability timeout** (`communication_availability_timeout`)
in the integration options. The default is **300 seconds**; supported values are
integers from **30 through 600**, inclusive. Booleans, fractions, strings and
out-of-range values are rejected. Missing or malformed legacy values use 300 s.
English and German forms document the option. Partial edits preserve polling,
diagnostic and other settings.

Timeout-only updates apply on the next existing coordinator refresh without a
reload, TCP reset, extra read, rescheduled deadline or cache/lease reset. Reducing
the timeout can expire an ongoing outage on that refresh. Increasing it extends a
pending grace period; an already unavailable source still needs a valid response
and valid source data before returning. A FAST/SLOW interval change continues to
use the established reload behavior.

## What counts as communication

Every configured device ID has its own monotonic failure interval. An actual
failed register exchange or failed connection needed to reach that unit starts
it. A valid successful register response from **that same ID** clears it, even if
values have not changed. Existing discovery reads and individual model chunks
count; TCP establishment alone does not. No traffic is added to measure health.

A successful response followed by a failed model/chunk starts a new interval at
the later failure. Repeated valid responses keep breaking that interval, so an
isolated failed model, missing decoded value or failed second chunk cannot make
a responding device expire. Unpolled SLOW models neither start failures nor count
as new responses. FAST=5 s with SLOW=300 s needs no special availability timer.
Successful Modbus responses do not validate an invalid SunSpec chain or decoded
value: existing identity/topology and decoder protections remain in force.

This observes the existing coordinator discovery/payload reads through a small
read-only view; original transports, endpoint ownership, request ordering and
control/write/readback paths are unchanged. Communication health is not an
alternative source of live write authority.

## Expiry and entity dependencies

Before expiry, existing hold-last behavior continues. Failed reads produce no
zeros, fresh reports or artificial efficiency samples. At `elapsed >= timeout`,
an existing coordinator refresh marks the affected read-only entities unavailable.
Their valid cached measurements remain in memory. Source identity and availability
suppression emit that transition once, with no repeated unavailable writes while
the outage persists. Restart/unload still discards session-local measurements;
persisted topology cannot restore a reading that was never measured.

- Raw Modbus measurement/status sensors depend on their device ID and model.
- PV power depends on Model 160 of its own unit; grid power on its unit's Model 203.
- Inverter/rectifier efficiency depends on Models 103/160 of that unit; battery
  lifetime efficiency on its Models 120/124/160.
- Consumption, autarky and self-consumption depend on the uniquely identified
  inverter (Model 103) **and** meter (Model 203), regardless of the entity's logical
  device attachment. Existing ambiguity rejection and formulas are unchanged.
- The read-only storage-control status follows its device timeout. Writable
  controls retain their immediate live-data and authority checks.
- Independent HTTP Solar API statuses retain their existing availability rules.

Thus a silent unit 200 sharing TCP with a responding inverter on unit 1 expires
independently: meter and site calculations become unavailable; inverter-only
measurements continue. Shared TCP resets still invalidate write authority using
the existing immediate session-generation rules.

## Recovery and diagnostics

A valid Modbus response clears the communication timeout. An expired raw source
remains unavailable until a new valid model snapshot replaces its pre-timeout
snapshot. Derived sensors need complete valid source calculations after every
required unit's timeout; they cannot revive a pre-timeout calculated result just
because some other model or discovery responded. Read-only control status needs
a freshly confirmed live classification. No cache or efficiency window is reset
merely because of timeout/recovery; normal precision, sample age and SLOW median
publication rules remain in place. Proven identity/topology changes still clear
incompatible history.

At diagnostic levels 1–3, structured `FPMGR subsystem=modbus` events report
`communication_availability_timeout` (`result="unavailable"`) and
`communication_availability_recovered` (`result="responding"`) with `entry_id`,
`device_id` and `timeout_seconds`. Each is emitted once per transition, without
five-minute suppression of a genuine new transition. Level 0 remains silent.
These events describe per-unit communication; they do not replace the existing
TCP, failure, session-reset, discovery, device or endpoint recovery diagnostics.
In particular, `responding` does not claim that all models or writes recovered.

## Validation

Deterministic tests cover the default and 30/600-second boundaries, invalid
options, live changes, unchanged reads, FAST/SLOW independence, partial model and
chunk failures, real HA state/report transitions, shared TCP units 1/200, derived
dependencies, offline startup, delayed source recovery, HTTP isolation, retained
storage status, efficiency windows and immediate write-authority rejection.
Existing polling, control, remote-lease and identity/topology suites also run.
**1,534 tests pass**, including 37 new parameterized regression cases. Ruff lint,
formatting checks for all 11 changed Python files, English/German translation-key
parity and `git diff --check` pass. The same 45 unrelated repository-wide formatting
findings remain. One existing Home Assistant/aiohttp deprecation warning remains.

All fault injection and test writes use synthetic clients. No Home Assistant
deployment, physical register writes, version change, merge or release is part of
this work.

## Changed files

- Runtime and options: `custom_components/fronius_pv_manager/{__init__,communication,
  config_flow,const,coordinator,measurement_entity,sensor,storage_status}.py`.
- Language files: `custom_components/fronius_pv_manager/strings.json` and
  `translations/{en,de}.json` in the same component.
- Tests: `tests/test_communication_availability.py`, `tests/test_polling_options.py`
  and `tests/test_diagnostic_logging.py`.
- Documentation: `README.md`, this document, `docs/diagnostics.md`,
  `docs/measurement-publication.md` and `docs/polling.md`.
