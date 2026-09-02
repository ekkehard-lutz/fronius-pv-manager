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

        async with self._coordinator.io_lock:
            result = await self._coordinator.hass.async_add_executor_job(
                self._write_once,
                transport,
                discovered,
                policy,
                value,
            )
        if not result.verified:
            raise WriteVerificationMismatchError(
                "write read-back does not match the requested value"
            )
        await self._coordinator.async_request_refresh()
        return FroniusPVWriteResult(device_id, policy, result)

    @staticmethod
    def _write_once(transport, discovered, policy, value) -> RegisterWriteResult:
        """Prepare immediately, perform one write, and classify failures."""
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
