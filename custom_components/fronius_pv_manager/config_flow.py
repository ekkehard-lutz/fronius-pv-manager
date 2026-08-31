"""Config flow for Fronius PV Manager Modbus TCP endpoints."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import CONF_DEVICE_IDS, DEFAULT_PORT, DOMAIN
from .model_decoder import decode_model
from .register_maps import MODEL_1
from .sunspec import SunSpecDiscovery, SunSpecDiscoveryError
from .transport import (
    ModbusConnectionError,
    ModbusTcpTransport,
    ModbusTransportError,
    read_holding_registers_chunked,
)

_LOGGER = logging.getLogger(__name__)
_MIN_DEVICE_ID = 1
_MAX_DEVICE_ID = 247

type TransportFactory = Callable[..., ModbusTcpTransport]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Stable identity metadata obtained during endpoint validation."""

    serial_number: str | None


def parse_device_ids(value: str) -> tuple[int, ...]:
    """Parse an ordered comma-separated list of readable Modbus device IDs."""
    if not value.strip():
        raise ValueError("device ID list must not be empty")
    parts = value.split(",")
    if any(not part.strip() for part in parts):
        raise ValueError("device ID list contains an empty element")
    try:
        device_ids = tuple(int(part.strip()) for part in parts)
    except ValueError as err:
        raise ValueError("device IDs must be integers") from err
    if any(
        not _MIN_DEVICE_ID <= device_id <= _MAX_DEVICE_ID
        for device_id in device_ids
    ):
        raise ValueError("device IDs must be between 1 and 247")
    if len(set(device_ids)) != len(device_ids):
        raise ValueError("device IDs must be unique")
    return device_ids


def _validate_endpoint(
    host: str,
    port: int,
    device_ids: tuple[int, ...],
    transport_factory: TransportFactory | None = None,
) -> ValidationResult:
    """Synchronously validate and close every requested SunSpec participant."""
    factory = transport_factory or ModbusTcpTransport
    transports: list[tuple[int, ModbusTcpTransport]] = []
    serials: list[tuple[int, bool, str]] = []
    try:
        for device_id in device_ids:
            transports.append(
                (
                    device_id,
                    factory(host, port=port, device_id=device_id),
                )
            )
        for device_id, transport in transports:
            try:
                transport.connect()
                discovered = SunSpecDiscovery(transport).discover()
                model_ids = {model.model_id for model in discovered}
                model_1 = next(
                    (model for model in discovered if model.model_id == 1), None
                )
                if model_1 is not None:
                    try:
                        payload = read_holding_registers_chunked(
                            transport,
                            model_1.base_address,
                            model_1.length,
                        )
                        serial = decode_model(MODEL_1, payload).fixed["SN"].value
                    except (ModbusTransportError, ValueError):
                        _LOGGER.debug(
                            "Could not read Model 1 identity from device ID %s",
                            device_id,
                            exc_info=True,
                        )
                    else:
                        if isinstance(serial, str) and (serial := serial.strip()):
                            serials.append((device_id, 103 in model_ids, serial))
            except Exception:
                _LOGGER.debug(
                    "Config flow validation failed for Modbus device ID %s",
                    device_id,
                    exc_info=True,
                )
                raise
    finally:
        for device_id, transport in transports:
            try:
                transport.close()
            except ModbusTransportError:
                _LOGGER.debug(
                    "Failed to close validation transport for device ID %s",
                    device_id,
                    exc_info=True,
                )
    serial_number = (
        min(serials, key=lambda candidate: (not candidate[1], candidate[0]))[2]
        if serials
        else None
    )
    return ValidationResult(serial_number)


def normalize_host(host: str) -> str:
    """Trim a host and remove one optional trailing DNS root dot."""
    normalized = host.strip()
    return normalized[:-1] if normalized.endswith(".") else normalized


def _endpoint_unique_id(host: str, port: int) -> str:
    """Return the temporary normalized endpoint duplicate identity."""
    return f"{normalize_host(host).casefold()}:{port}"


def _user_schema(user_input: dict[str, object] | None = None) -> vol.Schema:
    """Build the user form while preserving values after validation errors."""
    values = user_input or {}
    host = values.get(CONF_HOST)
    device_ids = values.get(CONF_DEVICE_IDS)
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST,
                **({"default": host} if host is not None else {}),
            ): str,
            vol.Optional(
                CONF_PORT,
                default=values.get(CONF_PORT, DEFAULT_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_DEVICE_IDS,
                **({"default": device_ids} if device_ids is not None else {}),
            ): str,
        }
    )


class FroniusPVManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one endpoint containing one or more Modbus participants."""

    VERSION = 1

    def _abort_if_endpoint_configured(self, host: str, port: int) -> None:
        """Abort on matching stored endpoint data regardless of permanent ID."""
        endpoint = _endpoint_unique_id(host, port)
        for entry in self._async_current_entries():
            stored_host = entry.data.get(CONF_HOST)
            stored_port = entry.data.get(CONF_PORT, DEFAULT_PORT)
            if (
                isinstance(stored_host, str)
                and _endpoint_unique_id(stored_host, stored_port) == endpoint
            ):
                raise data_entry_flow.AbortFlow("already_configured")

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        """Validate generic endpoint details supplied by the user."""
        errors = {}
        if user_input is not None:
            host = normalize_host(str(user_input[CONF_HOST]))
            port = int(user_input[CONF_PORT])
            try:
                device_ids = parse_device_ids(str(user_input[CONF_DEVICE_IDS]))
            except ValueError as err:
                error = str(err)
                errors[CONF_DEVICE_IDS] = (
                    "duplicate_device_ids"
                    if error == "device IDs must be unique"
                    else "invalid_device_ids"
                )
            else:
                if not host:
                    errors[CONF_HOST] = "invalid_host"
                else:
                    await self.async_set_unique_id(_endpoint_unique_id(host, port))
                    self._abort_if_unique_id_configured()
                    self._abort_if_endpoint_configured(host, port)
                    try:
                        validation = await self.hass.async_add_executor_job(
                            _validate_endpoint,
                            host,
                            port,
                            device_ids,
                        )
                    except ModbusConnectionError:
                        errors["base"] = "cannot_connect"
                    except SunSpecDiscoveryError:
                        errors["base"] = "invalid_sunspec"
                    except ModbusTransportError:
                        errors["base"] = "cannot_connect"
                    except Exception:
                        _LOGGER.exception(
                            "Unexpected exception validating Modbus endpoint"
                        )
                        errors["base"] = "unknown"
                    else:
                        if validation.serial_number is not None:
                            await self.async_set_unique_id(
                                f"fronius:{validation.serial_number}"
                            )
                            self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=host,
                            data={
                                CONF_HOST: host,
                                CONF_PORT: port,
                                CONF_DEVICE_IDS: list(device_ids),
                            },
                        )
            user_input = {**user_input, CONF_HOST: host}

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )
