from __future__ import annotations

from typing import Callable

from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
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
from .entity import device_info, AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    states = AmcStatesParser(coordinator.data)
    sensors: list[SensorEntity] = []

    def _notifications(_central_id):
        return lambda raw_state: AmcStatesParser(raw_state).notifications(_central_id)

    def _system_status(_central_id, index):
        return lambda raw_state: AmcStatesParser(raw_state).system_status(
            _central_id, index
        )

    for central_id in states.raw_states():
        sensors.append(
            AmcSignalSensor(
                coordinator=coordinator,
                device_info=device_info(states, central_id, coordinator),
                amc_entry=states.system_status(central_id, SystemStatusDataSections.GSM_SIGNAL),
                attributes_fn=_system_status(central_id, SystemStatusDataSections.GSM_SIGNAL),
                name_prefix=coordinator.get_config(CONF_STATUS_SYSTEM_PREFIX),
                id_prefix="system_status_",
            )
        )
        sensors.append(
            AmcBatterySensor(
                coordinator=coordinator,
                device_info=device_info(states, central_id, coordinator),
                amc_entry=states.system_status(central_id, SystemStatusDataSections.BATTERY_STATUS),
                attributes_fn=_system_status(central_id, SystemStatusDataSections.BATTERY_STATUS),
                name_prefix=coordinator.get_config(CONF_STATUS_SYSTEM_PREFIX),
                id_prefix="system_status_",
            )
        )

        sensors.append(
            AmcNotification(
                coordinator=coordinator,
                device_info=device_info(states, central_id, coordinator),
                amc_notifications=states.notifications(central_id),
                attributes_fn=_notifications(central_id)
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
        device_info: DeviceInfo,
        amc_notifications: list[AmcNotificationEntry],
        attributes_fn: Callable,
    ) -> None:
        super().__init__(coordinator)

        self._attributes_fn = attributes_fn
        self._amc_notifications = amc_notifications

        self._attr_name = "Notifications"
        self._attr_unique_id = str(CentralDataSections.NOTIFICATIONS)
        self._attr_device_info = device_info

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._amc_notifications: list[AmcNotificationEntry] = self._attributes_fn(
            self.coordinator.data
        )
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
