"""Setup lifecycle tests for account-wide login throttling."""

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


# The integration module's HA dependencies are intentionally minimal: these
# tests exercise only setup orchestration, not Home Assistant itself.
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "amc_alarm"


def _stub_module(name):
    return types.ModuleType(name)


ha = _stub_module("homeassistant")
ha.__path__ = []
config_entries = _stub_module("homeassistant.config_entries")
config_entries.ConfigEntry = type("ConfigEntry", (), {})
const = _stub_module("homeassistant.const")
const.CONF_EMAIL = "email"
const.CONF_PASSWORD = "password"
const.SERVICE_RELOAD = "reload"
const.Platform = type("Platform", (), {
    "ALARM_CONTROL_PANEL": "alarm_control_panel",
    "SENSOR": "sensor",
    "BINARY_SENSOR": "binary_sensor",
    "SWITCH": "switch",
})
core = _stub_module("homeassistant.core")
core.HomeAssistant = type("HomeAssistant", (), {})
exceptions = _stub_module("homeassistant.exceptions")


class ConfigEntryNotReady(Exception):
    pass


exceptions.ConfigEntryNotReady = ConfigEntryNotReady
exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
exceptions.ConfigEntryError = type("ConfigEntryError", (Exception,), {})
helpers = _stub_module("homeassistant.helpers")
helpers.__path__ = []
update_coordinator = _stub_module("homeassistant.helpers.update_coordinator")
update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
typing_module = _stub_module("homeassistant.helpers.typing")
typing_module.ConfigType = dict
service = _stub_module("homeassistant.helpers.service")
service.async_register_admin_service = lambda *args, **kwargs: None
issue_registry = _stub_module("homeassistant.helpers.issue_registry")
custom_components = _stub_module("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
coordinator_module = _stub_module("custom_components.amc_alarm.coordinator")
coordinator_module.AmcDataUpdateCoordinator = object
_stubbed_modules = {
    "homeassistant": ha,
    "homeassistant.config_entries": config_entries,
    "homeassistant.const": const,
    "homeassistant.core": core,
    "homeassistant.exceptions": exceptions,
    "homeassistant.helpers": helpers,
    "homeassistant.helpers.update_coordinator": update_coordinator,
    "homeassistant.helpers.typing": typing_module,
    "homeassistant.helpers.service": service,
    "homeassistant.helpers.issue_registry": issue_registry,
    "custom_components": custom_components,
    "custom_components.amc_alarm.coordinator": coordinator_module,
}
with mock.patch.dict(sys.modules, _stubbed_modules):
    sys.modules.pop("custom_components.amc_alarm", None)
    integration = importlib.import_module("custom_components.amc_alarm")


class FakeApi:
    def __init__(self, deadline=None):
        self._login_throttled_until = deadline
        self.disconnected = False
        self.disconnect_error = None

    async def disconnect(self):
        self.disconnected = True
        if self.disconnect_error:
            raise self.disconnect_error


class FakeCoordinator:
    instances = []
    refresh = None

    def __init__(self, hass, entry):
        self.api = FakeApi()
        type(self).instances.append(self)

    async def async_config_entry_first_refresh(self):
        await type(self).refresh(self)


class SetupCooldownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeCoordinator.instances = []
        self.now = 100.0
        self.loop = types.SimpleNamespace(time=lambda: self.now)
        self.hass = types.SimpleNamespace(
            data={"amc_alarm": {}}, loop=self.loop,
        )
        self.entry = types.SimpleNamespace(
            version=2, data={"email": "user@example.com"}, runtime_data=None,
        )
        self.patcher = mock.patch.object(
            integration, "AmcDataUpdateCoordinator", FakeCoordinator
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_throttle_cleans_up_and_blocks_setup_until_deadline(self):
        async def throttled(coordinator):
            coordinator.api._login_throttled_until = 700.0
            raise ConfigEntryNotReady("login throttled")

        FakeCoordinator.refresh = throttled
        with self.assertRaises(ConfigEntryNotReady):
            await integration.async_setup_entry(self.hass, self.entry)

        self.assertTrue(FakeCoordinator.instances[0].api.disconnected)
        self.assertIsNone(self.entry.runtime_data)
        self.assertEqual(
            self.hass.data["amc_alarm"]["_login_cooldowns"]["user@example.com"],
            700.0,
        )

        async def success(_coordinator):
            return None

        FakeCoordinator.refresh = success
        with self.assertRaises(ConfigEntryNotReady):
            await integration.async_setup_entry(self.hass, self.entry)
        self.assertEqual(len(FakeCoordinator.instances), 1)

        self.now = 700.0
        self.hass.config_entries = types.SimpleNamespace(
            async_forward_entry_setups=lambda *args: asyncio.sleep(0),
        )
        self.entry.async_on_unload = lambda callback: None
        self.entry.add_update_listener = lambda callback: None
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertEqual(len(FakeCoordinator.instances), 2)

    async def test_cancelled_first_refresh_disconnects_and_clears_runtime_data(self):
        async def cancelled(_coordinator):
            raise asyncio.CancelledError

        FakeCoordinator.refresh = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await integration.async_setup_entry(self.hass, self.entry)

        self.assertTrue(FakeCoordinator.instances[0].api.disconnected)
        self.assertIsNone(self.entry.runtime_data)

    async def test_disconnect_error_does_not_mask_first_refresh_error(self):
        original_error = RuntimeError("first refresh failed")

        async def failed(coordinator):
            coordinator.api.disconnect_error = OSError("disconnect failed")
            raise original_error

        FakeCoordinator.refresh = failed
        with self.assertLogs(integration._LOGGER, level="ERROR"):
            with self.assertRaises(RuntimeError) as caught:
                await integration.async_setup_entry(self.hass, self.entry)

        self.assertIs(caught.exception, original_error)
        self.assertTrue(FakeCoordinator.instances[0].api.disconnected)
        self.assertIsNone(self.entry.runtime_data)


if __name__ == "__main__":
    unittest.main()
