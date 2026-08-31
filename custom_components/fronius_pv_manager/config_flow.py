"""Config flow for Fronius PV Manager Modbus TCP endpoints."""

import logging
from collections.abc import Callable

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import CONF_DEVICE_IDS, DEFAULT_PORT, DOMAIN
from .sunspec import SunSpecDiscovery, SunSpecDiscoveryError
from .transport import (
    ModbusConnectionError,
    ModbusTcpTransport,
    ModbusTransportError,
)

_LOGGER = logging.getLogger(__name__)
_MIN_DEVICE_ID = 1
_MAX_DEVICE_ID = 247

type TransportFactory = Callable[..., ModbusTcpTransport]


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
) -> None:
    """Synchronously validate and close every requested SunSpec participant."""
    factory = transport_factory or ModbusTcpTransport
    transports: list[tuple[int, ModbusTcpTransport]] = []
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
                SunSpecDiscovery(transport).discover()
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


def _endpoint_unique_id(host: str, port: int) -> str:
    """Return a stable identity without DNS resolution or device semantics."""
    return f"{host.casefold().rstrip('.')}:{port}"


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

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        """Validate generic endpoint details supplied by the user."""
        errors = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
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
                    try:
                        await self.hass.async_add_executor_job(
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
