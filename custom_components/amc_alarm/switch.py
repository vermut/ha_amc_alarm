from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections
from .amc_alarm_api.api import AmcStatesParser
from .const import *
from .entity import AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    states = coordinator.data_parsed
    outputs: list[SwitchEntity] = []

    def _output(_central_id, amc_id):
        return lambda: coordinator.data_parsed.output(_central_id, amc_id)

    for central_id in coordinator.central_ids():
        if coordinator.get_config(CONF_OUTPUT_INCLUDED):
            outputs.extend(
                AmcOutput(
                    coordinator=coordinator,
                    amc_entry_fn=_output(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_OUTPUT_PREFIX),
                    id_prefix="output",
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
        api = self.coordinator.api
        code = self.coordinator.get_default_pin()
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 1, code)

    async def async_turn_off(self, **kwargs: Any) -> None:
        api = self.coordinator.api
        code = self.coordinator.get_default_pin()
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 0, code)
