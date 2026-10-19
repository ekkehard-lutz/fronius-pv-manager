"""Per-unit read communication health, independent of publication and TCP sessions."""

from .const import (
    CONF_COMMUNICATION_AVAILABILITY_TIMEOUT,
    DEFAULT_COMMUNICATION_AVAILABILITY_TIMEOUT,
    MAX_COMMUNICATION_AVAILABILITY_TIMEOUT,
    MIN_COMMUNICATION_AVAILABILITY_TIMEOUT,
)
from .transport import ModbusTransportError


def communication_timeout(options):
    """Use a safe default for malformed legacy options; never coerce booleans."""
    value = options.get(
        CONF_COMMUNICATION_AVAILABILITY_TIMEOUT,
        DEFAULT_COMMUNICATION_AVAILABILITY_TIMEOUT,
    )
    return (
        value
        if (
            type(value) is int
            and MIN_COMMUNICATION_AVAILABILITY_TIMEOUT
            <= value
            <= MAX_COMMUNICATION_AVAILABILITY_TIMEOUT
        )
        else DEFAULT_COMMUNICATION_AVAILABILITY_TIMEOUT
    )


class CommunicationReader:
    """Observe existing reads only, including discovery and individual chunks.

    This ephemeral view never owns/connects/resets a transport or authorizes writes.
    The original unit views and control/readback paths stay unchanged.
    """

    def __init__(self, transport, observe):
        self.transport = transport
        self.observe = observe

    def read_holding_registers(self, address, count):
        try:
            words = self.transport.read_holding_registers(address, count)
        except ModbusTransportError:
            self.observe(False)
            raise
        # An error/short/malformed response cannot prove successful communication.
        valid = len(words) == count and all(
            type(word) is int and 0 <= word <= 0xFFFF for word in words
        )
        self.observe(valid)
        return words
