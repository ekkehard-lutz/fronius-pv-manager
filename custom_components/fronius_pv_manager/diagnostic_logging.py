"""Small runtime-owned diagnostic emitter; no HA entities, I/O or timers."""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import RLock

from .const import CONF_DIAGNOSTIC_LEVEL

_LOGGER = logging.getLogger(__name__)
REPEAT_SECONDS = 300
SUMMARY_SECONDS = 60
MAX_EPISODE_SIGNATURES = 32


def diagnostic_level(options):
    """Fail closed for malformed stored options, without bool/int coercion."""
    value = options.get(CONF_DIAGNOSTIC_LEVEL, 0)
    return value if type(value) is int and 0 <= value <= 3 else 0


def diagnostic_observer(method):
    @wraps(method)
    def safe(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            return False

    return safe


def error_reason(error):
    """Return stable classifications, never arbitrary exception messages."""
    current = error
    for _ in range(4):
        if isinstance(current, TimeoutError):
            return "timeout"
        current = current.__cause__
        if current is None:
            break
    return getattr(
        error,
        "reason",
        "invalid_sunspec"
        if type(error).__name__ == "SunSpecDiscoveryError"
        else "decode_error",
    )


class Diagnostics:
    """Fixed runtime scopes and bounded error signatures, shared with transport.

    The lock covers worker/event-loop observations, never Modbus I/O. Disabled
    diagnostics retain only outage bookkeeping so live opt-in can see an outage.
    """

    def __init__(self, options, *, clock=None, entry_id=None):
        self.options = options
        self.clock = clock or time.monotonic
        self.entry_id = entry_id
        self._episodes = {}
        self._states = {}
        self._rates = {}
        self._lock = RLock()

    @diagnostic_observer
    def enabled(self, level):
        return diagnostic_level(self.options()) >= level

    @diagnostic_observer
    def active(self, scope):
        with self._lock:
            return scope in self._episodes

    @diagnostic_observer
    def event(self, level, subsystem, event, *, scope=None, interval=0, **fields):
        if not self.enabled(level):
            return False
        with self._lock:
            key = (scope, event)
            now = self.clock()
            if interval and now < self._rates.get(key, -float("inf")):
                return False
            record = {"event": event, **fields}
            if self.entry_id is not None:
                record["entry_id"] = self.entry_id
            _LOGGER.log(
                logging.ERROR if level == 0 else logging.WARNING,
                "FPMGR subsystem=%s %s",
                subsystem,
                json.dumps(record, sort_keys=True, allow_nan=False),
            )
            if interval:
                self._rates[key] = now + interval
            return True

    @diagnostic_observer
    def failure(self, scope, subsystem, event, *, reason, **fields):
        with self._lock:
            errors = self._episodes.setdefault(scope, {})
            signature = (event, reason)
            if signature not in errors and len(errors) >= MAX_EPISODE_SIGNATURES - 1:
                signature = ("other_failures", "signature_limit")
            deadline, repeats = errors.get(signature, (-float("inf"), 0))
            now = self.clock()
            if now < deadline:
                errors[signature] = (deadline, repeats + 1)
                return False
            emitted = self.event(
                1, subsystem, event, reason=reason, suppressed=repeats, **fields
            )
            errors[signature] = (
                now + REPEAT_SECONDS if emitted else deadline,
                0 if emitted else repeats + 1,
            )
            return emitted

    @diagnostic_observer
    def recover(self, scope, subsystem, event, **fields):
        with self._lock:
            if self._episodes.pop(scope, None) is not None:
                self._rates = {
                    key: value for key, value in self._rates.items() if key[0] != scope
                }
                self.event(1, subsystem, event, result="recovered", **fields)

    @diagnostic_observer
    def transition(self, scope, subsystem, event, value, **fields):
        if not self.enabled(2):
            return
        with self._lock:
            key = (scope, event)
            had_value = key in self._states
            previous = self._states.get(key)
            self._states[key] = value
            if had_value and previous != value:
                self.event(2, subsystem, event, **fields)


def diagnostic_operation(subsystem, expected_errors, *, quiet=False):
    """Observe existing async control boundaries without changing their ordering."""

    def decorate(method):
        @wraps(method)
        async def observed(self, *args, **kwargs):
            coordinator = getattr(self, "_coordinator", None)
            if coordinator is None:
                coordinator = self.coordinator
            diagnostics = coordinator.diagnostics
            device_id = args[0] if args else kwargs.get("device_id")
            fields = {"operation": method.__name__, "device_id": device_id}
            # Heartbeats are not operations worth logging at every renewal.
            if not quiet:
                diagnostics.event(2, subsystem, "control_requested", **fields)
            try:
                result = await method(self, *args, **kwargs)
            except Exception as err:
                diagnostics.event(
                    2 if isinstance(err, expected_errors) else 0,
                    subsystem,
                    "control_failed",
                    **fields,
                    reason=type(err).__name__,
                    result="failed",
                )
                raise
            if not quiet:
                diagnostics.event(
                    2,
                    subsystem,
                    "control_completed",
                    **fields,
                    result="verified" if subsystem == "control" else "completed",
                )
            return result

        return observed

    return decorate


_ACTIVE_MODBUS = ContextVar("fronius_pv_manager_modbus_diagnostics", default=None)


class _OwnedModbusFilter(logging.Filter):
    """Normalize only this runtime's synchronous pymodbus output.

    Other integrations and standalone clients keep their own logging behavior.
    No raw upstream message or frame dump is copied into our diagnostics.
    """

    def filter(self, record):
        diagnostics = _ACTIVE_MODBUS.get()
        if diagnostics is None:
            return True
        try:
            message = str(record.msg)
            expected = message.startswith(
                ("No response received", "Connection to", "Repeating....")
            )
            if record.levelno >= logging.ERROR and not expected:
                diagnostics.event(
                    0,
                    "modbus",
                    "upstream_error",
                    reason="unexpected_pymodbus_error",
                    scope=("upstream", record.funcName),
                    interval=SUMMARY_SECONDS,
                )
        except Exception:
            pass
        return False


@diagnostic_observer
def attach_modbus_logging():
    """Install one dormant, context-scoped filter; never change logger levels."""
    logger = logging.getLogger("pymodbus.logging")
    if not any(isinstance(f, _OwnedModbusFilter) for f in logger.filters):
        logger.addFilter(_OwnedModbusFilter())


@contextmanager
def modbus_logging(diagnostics):
    token = _ACTIVE_MODBUS.set(diagnostics)
    try:
        yield
    finally:
        _ACTIVE_MODBUS.reset(token)
