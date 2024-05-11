from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
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
    outputs: list[SwitchEntity] = []

    def _output(_central_id, amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).output(_central_id, amc_id)

    for central_id in states.raw_states():
        outputs.extend(
            AmcOutput(
                coordinator=coordinator,
                device_info=device_info(states, central_id),
                amc_entry=x,
                attributes_fn=_output(central_id, x.Id),
            )
            for x in states.outputs(central_id).list
        )

    async_add_entities(outputs, False)


class AmcOutput(AmcBaseEntity, SwitchEntity):
    _amc_group_id = CentralDataSections.OUTPUTS
    _attr_device_class = SwitchDeviceClass.SWITCH

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._attr_is_on = self._amc_entry.states.bit_on == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, False)
