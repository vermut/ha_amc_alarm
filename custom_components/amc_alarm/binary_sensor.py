from __future__ import annotations

from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from . import AmcStatesType
from .const import DOMAIN
from .entity import AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: DataUpdateCoordinator[AmcStatesType] = hass.data[DOMAIN][
        entry.entry_id
    ]
    binary_sensors: list[AmcEntryBinarySensor] = []

    data = coordinator.data
    data: AmcStatesType

    for central_id, central in data.items():
        for zone in central["ZONES"]:
            binary_sensors.append(
                AmcEntryBinarySensor(
                    coordinator=coordinator,
                    name=zone.name,
                    entry_id=entry.entry_id,
                    central_id=central_id,
                    amc_id=zone.Id,
                    amc_type="ZONES",
                )
            )

    async_add_devices(binary_sensors, True)


class AmcEntryBinarySensor(AmcBaseEntity, BinarySensorEntity):
    @property
    def is_on(self) -> Optional[bool]:
        """Return the state of the sensor."""
        return self._amc_entry.states.bit_opened == 1
