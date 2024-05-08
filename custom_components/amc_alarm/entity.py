from __future__ import annotations

from typing import Optional, Any, Callable

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from .amc_alarm_api.amc_proto import AmcCentralResponse, AmcEntry


class AmcBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        amc_entry: AmcEntry,
        attributes_fn: Callable[[dict[str, AmcCentralResponse]], AmcEntry],
    ) -> None:
        super().__init__(coordinator)

        self._attributes_fn = attributes_fn
        self._amc_entry = amc_entry

        self._attr_name = amc_entry.name
        self._attr_unique_id = str(amc_entry.Id)

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
