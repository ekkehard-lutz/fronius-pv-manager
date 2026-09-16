"""Home Assistant runtime for explicitly approved semantic register writes."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .register_writer import (
    RegisterWriteError,
    RegisterWriteResult,
    RegisterWriteVerificationError,
    execute_register_write,
    prepare_register_write,
)
from .transport import ModbusTransportError
from .write_policy import (
    WritePolicy,
    WritePolicyError,
    resolve_policy_definition,
    validate_policy_value,
)

if TYPE_CHECKING:
    from .coordinator import FroniusPVCoordinator


class FroniusPVWriteError(Exception):
    """Base error for one at-most-once Home Assistant runtime write."""


class WriteNotApprovedError(FroniusPVWriteError):
    """Raised when semantic coordinates have no explicit allow-list entry."""


class WriteDeviceNotConfiguredError(FroniusPVWriteError):
    """Raised when a requested Modbus device ID is not configured."""


class WriteModelNotDiscoveredError(FroniusPVWriteError):
    """Raised when a requested model is absent from current topology."""


class WriteInvalidValueError(FroniusPVWriteError):
    """Raised when semantic policy or encoder validation rejects a value."""


class WriteTransportError(FroniusPVWriteError):
    """Raised when preparation or the single write attempt fails in transport."""


class WriteReadBackError(FroniusPVWriteError):
    """Raised when the post-write verification read fails."""


class WriteVerificationMismatchError(FroniusPVWriteError):
    """Raised when read-back semantics differ from the requested value."""


@dataclass(frozen=True, slots=True)
class FroniusPVWriteResult:
    """Immutable verified result returned to future Home Assistant callers."""

    device_id: int
    policy: WritePolicy
    register_result: RegisterWriteResult

    @property
    def verified(self) -> bool:
        """Return the mandatory semantic read-back verification state."""
        return self.register_result.verified


class FroniusPVWriteRuntime:
    """Resolve policy and execute one serialized, verified register write."""

    def __init__(self, coordinator: FroniusPVCoordinator) -> None:
        self._coordinator = coordinator

    async def async_write(
        self,
        device_id: int,
        model_id: int,
        register_name: str,
        value: object,
    ) -> FroniusPVWriteResult:
        """Execute one approved write at most once and refresh after success."""
        transport = self._coordinator.transports.get(device_id)
        if transport is None:
            raise WriteDeviceNotConfiguredError(
                f"Modbus device ID {device_id} is not configured"
            )
        discovered = self._coordinator.discovered_models_by_device.get(device_id)
        if discovered is None or not any(
            model.model_id == model_id for model in discovered
        ):
            raise WriteModelNotDiscoveredError(
                f"model {model_id} is not discovered on Modbus device ID {device_id}"
            )
        policy = self._coordinator.write_policies.get((model_id, register_name))
        if policy is None:
            raise WriteNotApprovedError(
                f"register {model_id}:{register_name} is not approved for writes"
            )
        if not policy.enabled:
            raise WriteNotApprovedError(
                f"writes are disabled for register {model_id}:{register_name}"
            )
        definition = resolve_policy_definition(policy)
        try:
            validate_policy_value(policy, definition, value)
        except WritePolicyError as err:
            raise WriteInvalidValueError(str(err)) from err

        result = await self._coordinator.async_run_io(
            self._write_once, device_id, transport, policy, value
        )
        if not result.verified:
            raise WriteVerificationMismatchError(
                "write read-back does not match the requested value"
            )
        await self._coordinator.async_request_refresh()
        return FroniusPVWriteResult(device_id, policy, result)

    async def async_write_sequence(
        self, device_id, requests, *, safety_prefix=0, before_write=None
    ):
        """Preflight every step, then execute under one polling/write lock.

        A nonzero safety_prefix defers later preflight errors until that many
        safety steps have verified; used only to prioritize automatic release.
        No rollback or Modbus atomicity is promised. Cancellation waits for the
        executor to finish before releasing the I/O lock.
        """
        requests = tuple(requests)
        results = await self._coordinator.async_run_io(
            self._sequence_once, device_id, requests, safety_prefix, before_write
        )
        try:
            await self._coordinator.async_request_refresh()
        except Exception as err:
            if safety_prefix:
                raise WriteSequenceError(
                    "all cleanup writes verified, but refresh failed",
                    results,
                    "refresh",
                ) from err
            raise
        return results

    def _sequence_once(self, device_id, requests, safety_prefix=0, before_write=None):
        transport = self._coordinator.transports.get(device_id)
        if transport is None:
            raise WriteDeviceNotConfiguredError(f"device {device_id} is not configured")
        discovered = self._coordinator.live_models(device_id)
        plans = []
        for index, (model_id, name, value) in enumerate(requests):
            try:
                plans.append(
                    self._prepare_sequence_step(
                        transport, discovered, model_id, name, value
                    )
                )
            except FroniusPVWriteError as err:
                if not safety_prefix or index < safety_prefix:
                    raise
                # Preflight every restoration step, but do not let a policy
                # restore failure prevent the verified automatic safety prefix.
                plans.append(err)
        if before_write is not None:
            before_write(transport, discovered)
        results = []
        for index, prepared in enumerate(plans):
            if isinstance(prepared, Exception):
                raise WriteSequenceError(
                    f"restore preflight failed for {requests[index][1]}; "
                    f"{index} steps verified; failed step was not written",
                    tuple(results),
                    requests[index][1],
                ) from prepared
            policy, plan = prepared
            try:
                result = execute_register_write(transport, plan)
                if not result.verified:
                    raise WriteVerificationMismatchError("read-back mismatch")
            except (
                ModbusTransportError,
                RegisterWriteError,
                FroniusPVWriteError,
            ) as err:
                raise WriteSequenceError(
                    f"sequence failed at step {index + 1} ({plan.register.name}); "
                    f"{index} steps verified, failed step may have applied; "
                    "device state uncertain; no rollback attempted",
                    tuple(results),
                    plan.register.name,
                ) from err
            results.append(FroniusPVWriteResult(device_id, policy, result))
        return tuple(results)

    def _prepare_sequence_step(self, transport, discovered, model_id, name, value):
        if sum(model.model_id == model_id for model in discovered) != 1:
            raise WriteModelNotDiscoveredError(
                "sequence requires one unambiguous model"
            )
        policy = self._coordinator.write_policies.get((model_id, name))
        if policy is None or not policy.enabled:
            raise WriteNotApprovedError(f"writes disabled for {model_id}:{name}")
        try:
            validate_policy_value(policy, resolve_policy_definition(policy), value)
            plan = prepare_register_write(transport, discovered, model_id, name, value)
        except (WritePolicyError, RegisterWriteError) as err:
            raise WriteInvalidValueError(str(err)) from err
        except ModbusTransportError as err:
            raise WriteTransportError(
                "sequence preparation failed; no writes attempted"
            ) from err
        return policy, plan

    def _write_once(self, device_id, transport, policy, value) -> RegisterWriteResult:
        """Resolve live addressing inside the I/O lock, then write and verify."""
        discovered = self._coordinator.live_models(device_id)
        if not discovered:
            raise WriteModelNotDiscoveredError("live topology requires validation")
        try:
            prepared = prepare_register_write(
                transport,
                discovered,
                policy.model_id,
                policy.register_name,
                value,
            )
        except ModbusTransportError as err:
            raise WriteTransportError("failed to prepare register write") from err
        except RegisterWriteError as err:
            raise WriteInvalidValueError(str(err)) from err
        try:
            return execute_register_write(transport, prepared)
        except RegisterWriteVerificationError as err:
            raise WriteReadBackError(str(err)) from err
        except ModbusTransportError as err:
            raise WriteTransportError("register write failed") from err


class WriteSequenceError(FroniusPVWriteError):
    """A partial sequence with explicit verified progress and uncertain step."""

    def __init__(self, message, completed, failed_register):
        super().__init__(message)
        self.completed = completed
        self.failed_register = failed_register
