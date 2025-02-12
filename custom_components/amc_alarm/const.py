"""Constants for the Amc_alarm Integration."""
from homeassistant.const import Platform
from enum import StrEnum

DOMAIN = "amc_alarm"
NAME = "AMC Alarm"

# PLATFORMS SUPPORTED
PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]

CONF_TITLE = "title"
CONF_CENTRAL_ID = "central_id"
CONF_CENTRAL_USERNAME = "central_username"
CONF_CENTRAL_PASSWORD = "central_password"

CONF_USER_PIN = "user_pin"

CONF_FLOW_VERSION = "config_version"
CONF_FLOW_LAST_VERSION = 1


CONF_STATUS_SYSTEM_PREFIX = "sensor_status_system_prefix"

CONF_STATUS_GROUP_INCLUDED = "sensor_status_group_included"
CONF_STATUS_GROUP_PREFIX = "sensor_status_group_prefix"
CONF_STATUS_AREA_INCLUDED = "sensor_status_area_included"
CONF_STATUS_AREA_PREFIX = "sensor_status_area_prefix"
CONF_STATUS_ZONE_INCLUDED = "sensor_status_zone_included"
CONF_STATUS_ZONE_PREFIX = "sensor_status_zone_prefix"

CONF_ACP_GROUP_INCLUDED = "alarm_group_included"
CONF_ACP_GROUP_PREFIX = "alarm_group_prefix"
CONF_ACP_AREA_INCLUDED = "alarm_area_included"
CONF_ACP_AREA_PREFIX = "alarm_area_prefix"
CONF_ACP_ZONE_INCLUDED = "alarm_zone_included"
CONF_ACP_ZONE_PREFIX = "alarm_zone_prefix"

CONF_OUTPUT_INCLUDED = "output_included"
CONF_OUTPUT_PREFIX = "output_prefix"

DEFAULT_SCAN_INTERVAL = 30

# DATA COORDINATOR ATTRIBUTES
LAST_UPDATED = "last_updated"

#class AmcSection(StrEnum):
#    GROUPS = "GROUPS"
#    AREAS = "AREAS"
#    ZONES = "ZONES"
#    OUTPUTS = "OUTPUTS"
#    SYSTEM_STATUS = "SYSTEM_STATUS"
#    NOTIFICATIONS = "NOTIFICATIONS"

