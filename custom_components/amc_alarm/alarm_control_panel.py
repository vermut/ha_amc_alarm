from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_DISARMED,
    STATE_ALARM_TRIGGERED,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .amc_alarm_api.api import AmcStatesParser
from .const import DOMAIN
from .entity import AmcBaseEntity


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    states = AmcStatesParser(coordinator.data)
    alarms: list[AmcAreaGroup] = []

    def _group(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).group(_central_id, _amc_id)

    def _area(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).area(_central_id, _amc_id)

    for central_id in states.raw_states():
        alarms.extend(
            AmcAreaGroup(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_group(central_id, x.Id)
            ) for x in states.groups(central_id).list
        )
        alarms.extend(
            AmcAreaGroup(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_area(central_id, x.Id)
            ) for x in states.areas(central_id).list
        )

    async_add_entities(alarms, True)


class AmcAreaGroup(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_supported_features = (
        # TODO changes AlarmControlPanelEntityFeature.ARM_AWAY
    )

    @property
    def state(self) -> str | None:
        if self._amc_entry.states.bit_armed:
            if self._amc_entry.states.anomaly:
                return STATE_ALARM_TRIGGERED

            return STATE_ALARM_ARMED_AWAY

        return STATE_ALARM_DISARMED
