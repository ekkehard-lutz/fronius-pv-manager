"""Constants for Fronius PV Manager."""

DOMAIN = "fronius_pv_manager"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_IDS = "device_ids"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
CONF_FAST_SCAN_INTERVAL = "fast_scan_interval"
CONF_SLOW_SCAN_INTERVAL = "slow_scan_interval"
DEFAULT_FAST_SCAN_INTERVAL = 5
DEFAULT_SLOW_SCAN_INTERVAL = 30
MIN_FAST_SCAN_INTERVAL = 2
MAX_FAST_SCAN_INTERVAL = 30
MIN_SLOW_SCAN_INTERVAL = 10
MAX_SLOW_SCAN_INTERVAL = 300

# Whole payloads: AC electrical values, storage/SoC, DC channels, grid meter.
FAST_MODEL_IDS = frozenset({103, 124, 160, 203})

CONF_DIAGNOSTIC_LEVEL = "diagnostic_level"
