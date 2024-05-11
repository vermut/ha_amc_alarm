from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections
from .amc_alarm_api.api import AmcStatesParser
from .const import DOMAIN
from .entity import device_info, AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    states = AmcStatesParser(coordinator.data)
    sensors: list[BinarySensorEntity] = []

    def _system_status(_central_id, index):
        return lambda raw_state: AmcStatesParser(raw_state).system_status(
            _central_id, index
        )

    for central_id in states.raw_states():
        sensors.extend(
            AmcTamperSensor(
                coordinator=coordinator,
                device_info=device_info(states, central_id),
                amc_entry=x,
                attributes_fn=_system_status(central_id, x.index),
            )
            for x in states.system_statuses(central_id).list
        )

    async_add_entities(sensors, False)


class AmcTamperSensor(AmcBaseEntity, BinarySensorEntity):
    _amc_group_id = CentralDataSections.SYSTEM_STATUS
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._attr_is_on = self._amc_entry.states.anomaly == 1
