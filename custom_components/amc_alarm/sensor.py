from __future__ import annotations

from typing import Callable

from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
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

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._attr_native_value = int(
            self._amc_entry.states.progress / 15 * 100
        )  # 15 is max


class AmcSignalSensor(AmcBaseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._attr_native_value = self._amc_entry.states.progress


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
        # Riutilizza lo stesso DeviceInfo già creato
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


class DeviceStatusSensor(SensorEntity):
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_name = "Device Status"
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

    @property
    def native_value(self):
        """Stato della connessione websocket/API."""
        return "connected" if self.coordinator.device_available else "disconnected"

    @property
    def extra_state_attributes(self):
        """Aggiungi dettagli utili sullo stato della connessione."""
        api = self.coordinator.api
        central_data = api.raw_states()[api._central_id] if api.raw_states() and api._central_id in api.raw_states() else None
        #states = self.coordinator.data_parsed if self.coordinator.api._raw_states_central_valid else None        
        return {
            "last_error": getattr(self.coordinator, "last_error", None),
            "last_update": getattr(self.coordinator, "last_update_success_time", None),
            "retries": getattr(self.coordinator, "retry_count", 0),
            "ws_state": getattr(getattr(self.coordinator.api, "_ws_state", None), "name", "unknown"),
            "central_status": getattr(central_data, "status", None),
            "central_statusID": getattr(central_data, "statusID", None),
            #"raw_states_central_valid": getattr(self.coordinator.api, "_raw_states_central_valid", None),
            #"status_is_error": states.status_is_error(self.coordinator.api._central_id) if states else None,
            #"raw_data": self.coordinator.data,
        }
    
def getattr_nested(obj, attr_path, default=None):
    for attr in attr_path.split("."):
        obj = getattr(obj, attr, None)
        if obj is None:
            return default
    return obj