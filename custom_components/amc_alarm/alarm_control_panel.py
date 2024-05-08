from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_DISARMED,
    STATE_ALARM_TRIGGERED,
    STATE_ALARM_PENDING,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections
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
    alarms: list[AlarmControlPanelEntity] = []

    def _zone(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).zone(_central_id, _amc_id)

    def _group(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).group(_central_id, _amc_id)

    def _area(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).area(_central_id, _amc_id)

    for central_id in states.raw_states():
        alarms.extend(
            AmcAreaGroup(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_group(central_id, x.Id),
            )
            for x in states.groups(central_id).list
        )
        alarms.extend(
            AmcAreaGroup(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_area(central_id, x.Id),
            )
            for x in states.areas(central_id).list
        )
        alarms.extend(
            AmcZone(
                coordinator=coordinator,
                amc_entry=x,
                attributes_fn=_zone(central_id, x.Id),
            )
            for x in states.zones(central_id).list
        )

    async_add_entities(alarms, True)


class AmcZone(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    _amc_group_id = CentralDataSections.ZONES

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, True)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, False)

    @property
    def state(self) -> str | None:
        if self._amc_entry.states.anomaly:
            return STATE_ALARM_TRIGGERED

        match (self._amc_entry.states.bit_armed, self._amc_entry.states.bit_on):
            case (1, 1):
                return STATE_ALARM_ARMED_AWAY
            case (0, 1):
                return STATE_ALARM_PENDING
            case _:
                return STATE_ALARM_DISARMED


class AmcAreaGroup(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _amc_group_id = None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, True)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.hass.data[DOMAIN]["__api__"]
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, False)

    @property
    def state(self) -> str | None:
        match (self._amc_entry.states.bit_on, self._amc_entry.states.anomaly):
            case (1, 1):
                return STATE_ALARM_TRIGGERED
            case (1, 0):
                return STATE_ALARM_ARMED_AWAY
            case (0, 1):
                return STATE_ALARM_PENDING
            case (0, 0):
                return STATE_ALARM_DISARMED


class AmcArea(AmcAreaGroup):
    _amc_group_id = CentralDataSections.AREAS


class AmcGroup(AmcAreaGroup):
    _amc_group_id = CentralDataSections.GROUPS
