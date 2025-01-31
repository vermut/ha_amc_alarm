from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature, AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections
from .amc_alarm_api.api import AmcStatesParser
from .const import *
from .entity import AmcBaseEntity, device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    states = AmcStatesParser(coordinator.data)
    alarms: list[AlarmControlPanelEntity] = []

    def _zone(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).zone(_central_id, _amc_id)

    def _group(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).group(_central_id, _amc_id)

    def _area(_central_id, _amc_id):
        return lambda raw_state: AmcStatesParser(raw_state).area(_central_id, _amc_id)

    for central_id in states.raw_states():
        if coordinator.get_config(CONF_ACP_GROUP_INCLUDED):
            alarms.extend(
                AmcAreaGroup(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_group(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_GROUP_PREFIX),
                    id_prefix="alarm_group_",
                )
                for x in states.groups(central_id).list
            )
        if coordinator.get_config(CONF_ACP_AREA_INCLUDED):
            alarms.extend(
                AmcAreaGroup(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_area(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_AREA_PREFIX),
                    id_prefix="alarm_area_",
                )
                for x in states.areas(central_id).list
            )
        if coordinator.get_config(CONF_ACP_ZONE_INCLUDED):
            alarms.extend(
                AmcZone(
                    coordinator=coordinator,
                    device_info=device_info(states, central_id, coordinator),
                    amc_entry=x,
                    attributes_fn=_zone(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_ZONE_PREFIX),
                    id_prefix="alarm_zone_",
                )
                for x in states.zones(central_id).list
            )

    async_add_entities(alarms, False)


class AmcZone(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    _amc_group_id = CentralDataSections.ZONES

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        api = self.coordinator.api
        code = self.coordinator.get_user_pin(code)
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 1, code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.coordinator.api
        code = self.coordinator.get_user_pin(code)
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 0, code)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if self._amc_entry.states.anomaly:
            return AlarmControlPanelState.TRIGGERED

        match (self._amc_entry.states.bit_armed, self._amc_entry.states.bit_on):
            case (1, 1):
                return AlarmControlPanelState.ARMED_AWAY
            case (0, 1):
                return AlarmControlPanelState.PENDING
            case _:
                return AlarmControlPanelState.DISARMED


class AmcAreaGroup(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _amc_group_id = None

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        api = self.coordinator.api
        code = self.coordinator.get_user_pin(code)
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 1, code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.coordinator.api
        code = self.coordinator.get_user_pin(code)
        await api.command_set_states(self._amc_group_id, self._amc_entry.index, 0, code)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        match (self._amc_entry.states.bit_on, self._amc_entry.states.anomaly):
            case (1, 1):
                return AlarmControlPanelState.TRIGGERED
            case (1, 0):
                return AlarmControlPanelState.ARMED_AWAY
            case (0, 1):
                return AlarmControlPanelState.PENDING
            case (0, 0):
                return AlarmControlPanelState.DISARMED


class AmcArea(AmcAreaGroup):
    _amc_group_id = CentralDataSections.AREAS


class AmcGroup(AmcAreaGroup):
    _amc_group_id = CentralDataSections.GROUPS
