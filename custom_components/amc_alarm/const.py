"""Constants for the Amc_alarm Integration."""
from homeassistant.const import Platform

DOMAIN = "amc_alarm"
NAME = "AMC Alarm"

# PLATFORMS SUPPORTED
PLATFORMS = [Platform.BINARY_SENSOR, Platform.ALARM_CONTROL_PANEL]

# DATA COORDINATOR ATTRIBUTES
LAST_UPDATED = "last_updated"
