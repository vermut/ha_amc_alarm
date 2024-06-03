from __future__ import annotations

from typing import Optional, Any, Callable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from .amc_alarm_api.amc_proto import AmcCentralResponse, AmcEntry
from .amc_alarm_api.api import AmcStatesParser
from .const import DOMAIN


def device_info(states: AmcStatesParser, central_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, central_id)},
        manufacturer="AMC Elettronica",
        model=states.model(central_id),
        name=states.real_name(central_id),
    )


class AmcBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device_info: DeviceInfo,
        amc_entry: AmcEntry,
        attributes_fn: Callable[[dict[str, AmcCentralResponse]], AmcEntry],
    ) -> None:
        super().__init__(coordinator)

        self._attributes_fn = attributes_fn
        self._amc_entry = amc_entry

        self._attr_name = amc_entry.name or f"{type(self).__name__} {amc_entry.index}"
        self._attr_unique_id = (
            str(amc_entry.Id) or f"{type(self).__name__}{amc_entry.index}"
        )
        self._attr_device_info = device_info

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._amc_entry = self._attributes_fn(self.coordinator.data)

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    @property
    def extra_state_attributes(self) -> Optional[dict[str, Any]]:
        return self._amc_entry.dict()
