"""Constants for the Amc_alarm Integration."""
from homeassistant.const import Platform

DOMAIN = "amc_alarm"
NAME = "AMC Alarm"

# PLATFORMS SUPPORTED
PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]

# DATA COORDINATOR ATTRIBUTES
LAST_UPDATED = "last_updated"
