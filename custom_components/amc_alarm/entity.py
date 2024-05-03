from __future__ import annotations

from typing import Literal, Optional, Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from . import AmcStatesType, AmcEntry


class AmcBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[AmcStatesType],
        entry_id: str,
        name: str,
        central_id: str,
        amc_type: Literal["AREAS", "GROUPS", "ZONES"],
        amc_id: int,
    ) -> None:
        """Initialize the Toyota entity."""
        super().__init__(coordinator)

        self._central_id = central_id
        self._amc_type = amc_type
        self._amc_id = amc_id
        self._amc_entry: Optional[AmcEntry] = None

        self._attr_name = name
        self._attr_unique_id = (
            f"amc_{entry_id}_{central_id}/{amc_type.lower()}_{amc_id}"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data: AmcStatesType = self.coordinator.data
        self._amc_entry = next(
            x for x in data[self._central_id][self._amc_type] if x.Id == self._amc_id
        )

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> Optional[dict[str, Any]]:
        return {**self._amc_entry.dict(), "AMC Type": self._amc_type}
