from __future__ import annotations

from typing import Callable

from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS, EntityCategory
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import (
    CentralDataSections,
    AmcNotificationEntry,
    SystemStatusDataSections,
)
from .amc_alarm_api.api import AmcStatesParser
from .const import *
from .entity import AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    states = coordinator.data_parsed
    sensors: list[SensorEntity] = []
    
    sensors.append(DeviceStatusSensor(coordinator=coordinator))
    sensors.append(DeviceStatusConnectivitySensor(coordinator=coordinator))

    def _notifications(_central_id):
        return lambda: coordinator.data_parsed.notifications(_central_id)

    def _system_status(_central_id, index):
        return lambda: coordinator.data_parsed.system_status(_central_id, index)

    for central_id in coordinator.central_ids():
        sensors.append(
            AmcSignalSensor(
                coordinator=coordinator,
                amc_entry_fn=_system_status(central_id, SystemStatusDataSections.GSM_SIGNAL),
                name_prefix=coordinator.get_config(CONF_STATUS_SYSTEM_PREFIX),
                id_prefix="system_status_",
            )
        )
        sensors.append(
            AmcBatterySensor(
                coordinator=coordinator,
                amc_entry_fn=_system_status(central_id, SystemStatusDataSections.BATTERY_STATUS),
                name_prefix=coordinator.get_config(CONF_STATUS_SYSTEM_PREFIX),
                id_prefix="system_status_",
            )
        )

        sensors.append(
            AmcNotification(
                coordinator=coordinator,
                amc_notifications_fn=_notifications(central_id)
            )
        )

    async_add_entities(sensors, False)


class AmcBatterySensor(AmcBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = (EntityCategory.DIAGNOSTIC)
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        # 15 is max
        self._attr_native_value = int(self._amc_entry.states.progress / 15 * 100) if self._amc_entry.states.progress > 0 else 0


class AmcSignalSensor(AmcBaseEntity, SensorEntity):
    #_attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = (EntityCategory.DIAGNOSTIC)
    _attr_state_class = SensorStateClass.MEASUREMENT

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        # 15 is max, value is in % not db
        self._attr_native_value = int(self._amc_entry.states.progress / 15 * 100) if self._amc_entry.states.progress > 0 else 0

    @property
    def icon(self):
        """Return the icon MDI from signal value."""
        if self.registry_entry and self.registry_entry.icon:
            return self.registry_entry.icon
        perc = self._attr_native_value
        if perc is None:
            return "mdi:network-strength-outline"
        elif perc > 80:
            return "mdi:network-strength-4"
        elif perc > 60:
            return "mdi:network-strength-3"
        elif perc > 40:
            return "mdi:network-strength-2"
        elif perc > 20:
            return "mdi:network-strength-1"
        else:
            return "mdi:network-strength-outline"

class AmcNotification(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmcDataUpdateCoordinator,
        amc_notifications_fn: Callable[[],list[AmcNotificationEntry]],
    ) -> None:
        super().__init__(coordinator)

        self._amc_notifications_fn = amc_notifications_fn
        self._amc_notifications = amc_notifications = amc_notifications_fn()

        self._attr_name = "Notifications"
        self._attr_unique_id = coordinator.get_id_prefix() + str(CentralDataSections.NOTIFICATIONS)

    @property
    def available(self):
        return self.coordinator.device_available

    @property
    def device_info(self):
        # Reuse the same DeviceInfo already created
        return self.coordinator.device_info

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._amc_notifications: list[AmcNotificationEntry] = self._amc_notifications_fn()
        if self._amc_notifications:
            notification = self._amc_notifications[0]
            self._attr_native_value = notification.name

            self._attr_extra_state_attributes = {
                x.serverDate: x.name for x in self._amc_notifications
            }

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()


class DeviceStatusSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = (EntityCategory.DIAGNOSTIC)

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._attr_name = "Device Status"
        self._attr_unique_id = f"{coordinator.get_id_prefix()}_status"
        #self._attr_device_class = "connectivity"

    @property
    def device_info(self):
        # Riutilizza lo stesso DeviceInfo già creato
        return self.coordinator.device_info

    @property
    def available(self):
        """Alwais available"""
        return True

    @property
    def native_value(self):
        """Status of websocket/API."""
        api = self.coordinator.api
        res = getattr(getattr(api, "_ws_state", None), "name", "unknown").replace("_", " ").capitalize()
        det = getattr(api, "_ws_state_detail", None)
        if det:
            res = f"{res} {det}"
        return res

        
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        api = self.coordinator.api
        central_data = api.raw_states()[api._central_id] if api.raw_states() and api._central_id in api.raw_states() else None
        res = getattr(getattr(api, "_ws_state", None), "name", "unknown").replace("_", " ").capitalize()
        det = getattr(api, "_ws_state_detail", None)
        if det:
            res = f"{res} {det}"
        self._attr_native_value = res
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()
    
    @property
    def extra_state_attributes(self):
        """Aggiungi dettagli utili sullo stato della connessione."""
        data = {
            #"last_error": getattr(self.coordinator, "last_error", None),
            #"last_update": getattr(self.coordinator, "last_update_success_time", None),
            "retries": getattr(self.coordinator, "retry_count", 0)
        }
        data.update(self.coordinator.api._get_status_info_dict())
        return data
    
class DeviceStatusConnectivitySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = (EntityCategory.DIAGNOSTIC)

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._attr_name = "Device Connectivity"
        self._attr_unique_id = f"{coordinator.get_id_prefix()}_connectivity"
        self._attr_device_class = "connectivity"

    @property
    def device_info(self):
        # Riutilizza lo stesso DeviceInfo già creato
        return self.coordinator.device_info

    @property
    def available(self):
        """Sempre disponibile, indipendentemente dallo stato del device."""
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()    

    @property
    def native_value(self):
        """Stato della connessione websocket/API."""
        return "connected" if self.coordinator.device_available else "disconnected"

    @property
    def extra_state_attributes(self):
        """Aggiungi dettagli utili sullo stato della connessione."""
        data = {
            #"last_error": getattr(self.coordinator, "last_error", None),
            #"last_update": getattr(self.coordinator, "last_update_success_time", None),
            "retries": getattr(self.coordinator, "retry_count", 0)
        }
        data.update(self.coordinator.api._get_status_info_dict())
        return data
    
def getattr_nested(obj, attr_path, default=None):
    for attr in attr_path.split("."):
        obj = getattr(obj, attr, None)
        if obj is None:
            return default
    return obj