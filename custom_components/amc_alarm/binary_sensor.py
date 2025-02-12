from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections
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
    sensors: list[BinarySensorEntity] = []

    def _zone(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).zone(_central_id, _amc_id)

    def _group(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).group(_central_id, _amc_id)

    def _area(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).area(_central_id, _amc_id)

    def _system_status(_central_id, index):
        return lambda raw_state: AmcStatesParser(raw_state).system_status(_central_id, index)

    for central_id in coordinator.central_ids():
        sensors.extend(
            AmcTamperSensor(
                coordinator=coordinator,
                device_info=device_info(states, central_id, coordinator),
                amc_entry=x,
                attributes_fn=_system_status(central_id, x.index),
                name_prefix=coordinator.get_config(CONF_STATUS_SYSTEM_PREFIX),
                id_prefix="system_status_",
            )
            for x in states.system_statuses(central_id).list
        )

        if coordinator.get_config(CONF_STATUS_GROUP_INCLUDED):
            for x in states.groups(central_id).list:
                sensor = AmcZoneSensor(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_group(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_STATUS_GROUP_PREFIX),
                    id_prefix="group_status_",
                )
                sensor._amc_group_id = CentralDataSections.GROUPS
                sensors.append(sensor)
        if coordinator.get_config(CONF_STATUS_AREA_INCLUDED):
            for x in states.areas(central_id).list:
                sensor = AmcZoneSensor(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_area(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_STATUS_AREA_PREFIX),
                    id_prefix="area_status_",
                )
                sensor._amc_group_id = CentralDataSections.AREAS
                sensors.append(sensor)
        if coordinator.get_config(CONF_STATUS_ZONE_INCLUDED):
            for x in states.zones(central_id).list:
                sensor = AmcZoneSensor(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_zone(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_STATUS_ZONE_PREFIX),
                    id_prefix="zone_status_",
                )
                sensor._amc_group_id = CentralDataSections.ZONES
                sensors.append(sensor)

    async_add_entities(sensors, False)


class AmcTamperSensor(AmcBaseEntity, BinarySensorEntity):
    _amc_group_id = CentralDataSections.SYSTEM_STATUS
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._attr_is_on = self._amc_entry.states.anomaly == 1


class AmcZoneSensor(AmcBaseEntity, BinarySensorEntity):
    _amc_group_id = CentralDataSections.ZONES
    # https://www.home-assistant.io/integrations/binary_sensor/#device-class
    # https://sci-git.cs.rptu.de/s_menne19/hassio-core/-/blob/2023.4.0/homeassistant/components/binary_sensor/__init__.py
    
    #_attr_device_class = "motion"
    #icon: str = "mdi:motion-sensor"
    #icon_off: str = "mdi:motion-sensor-off"

    #@callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        self._attr_is_on = self._amc_entry.states.anomaly == 1
        #self.async_write_ha_state()

    @property
    def icon(self) -> str | None:
        """Icon of the sensor."""
        #if self.is_on is False:
        dclass = self.device_class if self.device_class else "motion"
        #get device class customized
        if self.registry_entry and self.registry_entry.device_class:
            dclass = self.registry_entry.device_class
        if self.registry_entry and self.registry_entry.icon:
            return self.registry_entry.icon
        icon = get_icon(dclass, self.is_on)
        #return str(self.registry_entry.icon) + "XX"
        if icon:
            return icon
        return super().icon

    #@property
    #def available(self):
    #    return self._amc_entry.states.bit_notReady == 0


def get_icon(device_class: str, isOn: bool) -> str:
    # https://www.home-assistant.io/integrations/binary_sensor/#device-class
    if not device_class:
        return None
    match device_class:
        case "motion":
            return "mdi:motion-sensor" if isOn else "mdi:motion-sensor-off"
        case "moving":
            return "mdi:motion-sensor" if isOn else "mdi:motion-sensor-off"
        case "occupancy":
            return "mdi:motion-sensor" if isOn else "mdi:motion-sensor-off"
        case "window":
            return "mdi:window-open" if isOn else "mdi:window-closed"
        case "door":
            return "mdi:door-open" if isOn else "mdi:door"
        case "garage_door":
            return "mdi:garage-open" if isOn else "mdi:garage"
    return None
