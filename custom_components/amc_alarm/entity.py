from __future__ import annotations

from typing import Optional, Any, Callable

from homeassistant.core import callback
from homeassistant.util import slugify
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import AmcCentralResponse, AmcEntry
from .amc_alarm_api.api import AmcStatesParser
from .const import DOMAIN, CONF_TITLE



class AmcBaseEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    coordinator: AmcDataUpdateCoordinator | None = None

    def __init__(
        self,
        coordinator: AmcDataUpdateCoordinator,
        amc_entry_fn: Callable[[], AmcEntry],
        name_prefix: str,
        id_prefix: str
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator

        self._amc_entry_fn = amc_entry_fn
        self._amc_entry = amc_entry = self._amc_entry_fn()

        self._attr_name = ((name_prefix or "").strip() + " " + (amc_entry.name or f"{type(self).__name__} {amc_entry.index}").strip()).strip()
        if len(name_prefix or "") > 0:
            id_prefix = (id_prefix + "_" + slugify(name_prefix.strip().lower())).strip("_ ")
        if len(id_prefix or "") > 0:
            id_prefix = id_prefix.strip("_ ") + "_"
        self._attr_unique_id = coordinator.get_id_prefix() + id_prefix + (
            str(amc_entry.Id) or f"{type(self).__name__}{amc_entry.index}"
        )
        
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
        self._amc_entry = self._amc_entry_fn()

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    @property
    def extra_state_attributes(self) -> Optional[dict[str, Any]]:
        return self._amc_entry.dict()
