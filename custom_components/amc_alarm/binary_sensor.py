from __future__ import annotations

from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .amc_alarm_api.api import AmcStatesParser
from .const import DOMAIN
from .entity import AmcBaseEntity


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    states = AmcStatesParser(coordinator.data)
    binary_sensors: list[AmcZone] = []

    def _zone(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).zone(_central_id, _amc_id)

    for central_id in states.raw_states():
        binary_sensors.extend(
            AmcZone(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_zone(central_id, x.Id)
            ) for x in states.zones(central_id).list
        )

    async_add_devices(binary_sensors, True)


class AmcZone(AmcBaseEntity, BinarySensorEntity):
    @property
    def is_on(self) -> Optional[bool]:
        """Return the state of the sensor."""
        return self._amc_entry.states.bit_opened == 1
