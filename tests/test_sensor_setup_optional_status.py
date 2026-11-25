"""Exercise the real sensor platform setup with lightweight Home Assistant stubs."""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "amc_alarm"


def _module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


def _load_sensor_module():
    """Load sensor.py while replacing HA and integration dependencies with stubs."""
    homeassistant = _module("homeassistant")
    homeassistant.__path__ = []
    _module(
        "homeassistant.const",
        PERCENTAGE="%",
        SIGNAL_STRENGTH_DECIBELS="dBm",
        EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
    )
    _module(
        "homeassistant.components.sensor",
        SensorEntity=type("SensorEntity", (), {}),
        SensorDeviceClass=SimpleNamespace(BATTERY="battery"),
        SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
    )
    _module("homeassistant.components")
    _module("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}))
    _module(
        "homeassistant.core",
        HomeAssistant=type("HomeAssistant", (), {}),
        callback=lambda fn: fn,
    )
    _module("homeassistant.helpers")
    _module(
        "homeassistant.helpers.entity_platform",
        AddEntitiesCallback=type("AddEntitiesCallback", (), {}),
    )

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        def _handle_coordinator_update(self):
            pass

        async def async_added_to_hass(self):
            pass

    _module(
        "homeassistant.helpers.update_coordinator",
        CoordinatorEntity=CoordinatorEntity,
    )

    # Provide package namespaces so loading the real sensor module doesn't import
    # the integration's HA-dependent __init__, coordinator, or entity modules.
    custom_components = _module("custom_components")
    custom_components.__path__ = []
    integration = _module("custom_components.amc_alarm")
    integration.__path__ = [str(INTEGRATION)]
    api_package = _module("custom_components.amc_alarm.amc_alarm_api")
    api_package.__path__ = []
    _module(
        "custom_components.amc_alarm.amc_alarm_api.amc_proto",
        CentralDataSections=SimpleNamespace(NOTIFICATIONS=5),
        AmcNotificationEntry=type("AmcNotificationEntry", (), {}),
        SystemStatusDataSections=SimpleNamespace(GSM_SIGNAL=0, BATTERY_STATUS=1),
    )
    _module(
        "custom_components.amc_alarm.amc_alarm_api.api",
        AmcStatesParser=type("AmcStatesParser", (), {}),
    )
    _module(
        "custom_components.amc_alarm.coordinator",
        AmcDataUpdateCoordinator=type("AmcDataUpdateCoordinator", (), {}),
    )
    _module(
        "custom_components.amc_alarm.const",
        CONF_STATUS_SYSTEM_PREFIX="status_prefix",
    )

    class AmcBaseEntity(CoordinatorEntity):
        def __init__(self, coordinator, amc_entry_fn, name_prefix, id_prefix):
            super().__init__(coordinator)
            # Match the relevant behavior of the production entity base: resolve its
            # entry during construction, exposing any lookup regression in setup.
            self._amc_entry = amc_entry_fn()

    _module("custom_components.amc_alarm.entity", AmcBaseEntity=AmcBaseEntity)

    spec = importlib.util.spec_from_file_location(
        "custom_components.amc_alarm.sensor", INTEGRATION / "sensor.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


with patch.dict(sys.modules):
    SENSOR = _load_sensor_module()


class SensorSetupOptionalStatusTests(unittest.TestCase):
    def run_setup(self, status_indices, section_present=True):
        statuses = [SimpleNamespace(index=index) for index in status_indices]

        class ParsedStates:
            def system_statuses(self, _central_id):
                # Mirrors the parser's empty-list fallback for a missing section.
                return SimpleNamespace(list=statuses if section_present else [])

            def system_status(self, _central_id, index):
                entry = next((entry for entry in statuses if entry.index == index), None)
                if entry is None:
                    raise AssertionError("setup requested an absent system-status entry")
                return entry

            def notifications(self, _central_id):
                return []

        class Coordinator:
            data_parsed = ParsedStates()

            def central_ids(self):
                return ["central"]

            def get_config(self, _key):
                return ""

            def get_id_prefix(self):
                return "central_"

        entry = SimpleNamespace(runtime_data=Coordinator())
        added = []

        async def setup():
            await SENSOR.async_setup_entry(
                None, entry, lambda entities, update_before_add: added.extend(entities)
            )

        asyncio.run(setup())
        return [type(entity).__name__ for entity in added]

    def test_gsm_absent_battery_present(self):
        names = self.run_setup([SENSOR.SystemStatusDataSections.BATTERY_STATUS])

        self.assertNotIn("AmcSignalSensor", names)
        self.assertIn("AmcBatterySensor", names)
        self.assertIn("DeviceStatusSensor", names)
        self.assertIn("DeviceStatusConnectivitySensor", names)
        self.assertIn("AmcNotification", names)

    def test_gsm_and_battery_present(self):
        names = self.run_setup(
            [
                SENSOR.SystemStatusDataSections.GSM_SIGNAL,
                SENSOR.SystemStatusDataSections.BATTERY_STATUS,
            ]
        )

        self.assertIn("AmcSignalSensor", names)
        self.assertIn("AmcBatterySensor", names)

    def test_gsm_and_battery_absent(self):
        names = self.run_setup([])

        self.assertNotIn("AmcSignalSensor", names)
        self.assertNotIn("AmcBatterySensor", names)
        self.assertIn("DeviceStatusSensor", names)
        self.assertIn("DeviceStatusConnectivitySensor", names)
        self.assertIn("AmcNotification", names)

    def test_system_status_section_absent(self):
        names = self.run_setup([], section_present=False)

        self.assertNotIn("AmcSignalSensor", names)
        self.assertNotIn("AmcBatterySensor", names)


if __name__ == "__main__":
    unittest.main()
