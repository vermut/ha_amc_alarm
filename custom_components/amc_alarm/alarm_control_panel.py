from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature, AlarmControlPanelState, CodeFormat
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import AmcDataUpdateCoordinator
from .amc_alarm_api.amc_proto import CentralDataSections, AmcEntry, AmcAlarmState
from .amc_alarm_api.api import AmcStatesParser
from .const import *
from .entity import AmcBaseEntity
from typing import List


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    states = coordinator.data_parsed
    alarms: list[AlarmControlPanelEntity] = []

    alarms.append(AmcGeneralAlarm(coordinator=coordinator))

    def _zone(_central_id, _amc_id):
        return lambda: coordinator.data_parsed.zone(_central_id, _amc_id)

    def _group(_central_id, _amc_id):
        return lambda: coordinator.data_parsed.group(_central_id, _amc_id)

    def _area(_central_id, _amc_id):
        return lambda: coordinator.data_parsed.area(_central_id, _amc_id)
    
    for central_id in coordinator.central_ids():
        if coordinator.get_config(CONF_ACP_GROUP_INCLUDED):            
            for x in states.groups(central_id).list:
                sensor = AmcEntryAlarmEntity(
                    coordinator=coordinator,
                    amc_entry_fn=_group(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_GROUP_PREFIX),
                    id_prefix="alarm_group_",
                )
                alarms.append(sensor)

        if coordinator.get_config(CONF_ACP_AREA_INCLUDED):
            for x in states.areas(central_id).list:
                sensor = AmcEntryAlarmEntity(
                    coordinator=coordinator,
                    amc_entry_fn=_area(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_AREA_PREFIX),
                    id_prefix="alarm_area_",
                )
                alarms.append(sensor)

        if coordinator.get_config(CONF_ACP_ZONE_INCLUDED):
            for x in states.zones(central_id).list:
                sensor = AmcEntryAlarmEntity(
                    coordinator=coordinator,
                    amc_entry_fn=_zone(central_id, x.Id),
                    name_prefix=coordinator.get_config(CONF_ACP_ZONE_PREFIX),
                    id_prefix="alarm_zone_",
                )
                alarms.append(sensor)

    async_add_entities(alarms, False)


class AmcGeneralAlarm(AmcBaseEntity, AlarmControlPanelEntity):
    def __init__(
        self,
        coordinator: AmcDataUpdateCoordinator,
    ) -> None:

        # Chiamo il costruttore della base con coordinator e entry fake solo per avere il name e riuso dei metodi di callback
        #use of construct for bypass validations 
        amc_entry = AmcEntry.construct(name="Alarm",group=100,Id=1)
        super().__init__(
            coordinator=coordinator,
            amc_entry_fn=lambda: amc_entry,
            name_prefix="",
            id_prefix="alarm_general_"
        )

        self._feature_data: dict[int, dict[str, Any]] = {}  # <-- feature -> dati multipli
        self._attr_supported_features = AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        self._init_sup_feat(CONF_GACP_HOME_IDS, AlarmControlPanelEntityFeature.ARM_HOME, AlarmControlPanelState.ARMED_HOME)
        self._init_sup_feat(CONF_GACP_AWAY_IDS, AlarmControlPanelEntityFeature.ARM_AWAY, AlarmControlPanelState.ARMED_AWAY)
        self._init_sup_feat(CONF_GACP_NIGHT_IDS, AlarmControlPanelEntityFeature.ARM_NIGHT, AlarmControlPanelState.ARMED_NIGHT)
        self._init_sup_feat(CONF_GACP_VACATION_IDS, AlarmControlPanelEntityFeature.ARM_VACATION, AlarmControlPanelState.ARMED_VACATION)
        self._init_sup_feat(CONF_GACP_CUSTOM_BYPASS_IDS, AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS, AlarmControlPanelState.ARMED_CUSTOM_BYPASS)
        
        #self._attr_name = "Alarm"
        #self._attr_unique_id = coordinator.get_id_prefix() + "Alarm"

    def _init_sup_feat(self, cfg_key_ids, feature, armed_state):
        ids = self.coordinator.get_config(cfg_key_ids) or []
        if len(ids) > 0:
            #sorted for activate group, and after the areas
            ids_sorted = sorted(ids, key=lambda x: int(x.split(".")[0]))
            self._attr_supported_features = self._attr_supported_features | feature
            self._feature_data[feature] = {
                "feature": feature,
                "ids": ids_sorted,
                "enabled": True,
                "armed_state": armed_state,
            }
        
    @property
    def code_format(self) -> CodeFormat | None:
        if not self.coordinator.api.pin_required:
            return None
        if self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True) and self.coordinator.get_config(CONF_ACP_DISARM_WITHOUT_PIN, True):
            return None
        if self.alarm_state == AlarmControlPanelState.PENDING:
            return None
        # PIN obbligatorio numerico
        return CodeFormat.NUMBER
    
    @property
    def code_arm_required(self) -> bool:
        """Se True, il codice serve anche per ARM."""        
        if self.coordinator.api.pin_required and not self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True):
            return True
        return False

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._async_alarm_arm_feature(AlarmControlPanelEntityFeature.ARM_HOME, code)
    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._async_alarm_arm_feature(AlarmControlPanelEntityFeature.ARM_AWAY, code)
    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._async_alarm_arm_feature(AlarmControlPanelEntityFeature.ARM_NIGHT, code)
    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._async_alarm_arm_feature(AlarmControlPanelEntityFeature.ARM_VACATION, code)
    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self._async_alarm_arm_feature(AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS, code)

    async def _async_alarm_arm_feature(self, feature : AlarmControlPanelEntityFeature, code: str | None = None) -> None:
        api = self.coordinator.api
        if not code and self.coordinator.api.pin_required and self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True):
            code = self.coordinator.get_default_pin()
        if feature not in self._feature_data:
            feature_name = AlarmControlPanelEntityFeature(feature).name.replace("ARM_", "")
            raise KeyError(f"Arm {feature_name} not supported, no entities selected in wizard.")
        data = self._feature_data[feature]
        entries = self._get_entries(data)
        processed_ids = set()  # set esistente
        for amc_entry in entries:
            # skip if not disarmed
            if amc_entry.arm_state != AmcAlarmState.Disarmed:
                continue
            #if selected an area included in a group, ignore it
            if amc_entry.filters and any(f in processed_ids for f in amc_entry.filters):
                continue
            # mark this entry as processed
            processed_ids.add(amc_entry.filter_id)
            # send the command
            await api.command_set_states(amc_entry.group, amc_entry.index, 1, code)


    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.coordinator.api
        if not code and self.coordinator.api.pin_required and self.coordinator.get_config(CONF_ACP_DISARM_WITHOUT_PIN, True):
            code = self.coordinator.get_default_pin()
        if not code and self.coordinator.api.pin_required and self.alarm_state == AlarmControlPanelState.PENDING:
            code = self.coordinator.get_default_pin()
        #disarm all
        state = AmcStatesParser(api.raw_states())
        entries = [
            e for e in [*state.groups(api._central_id).list, *state.areas(api._central_id).list]
            if e.arm_state != AmcAlarmState.Disarmed
        ]
        processed_ids = set()  # set esistente
        for amc_entry in entries:
            #if selected an area included in a group, ignore it
            if amc_entry.filters and any(f in processed_ids for f in amc_entry.filters):
                continue
            # mark this entry as processed
            processed_ids.add(amc_entry.filter_id)
            # send the command
            await api.command_set_states(amc_entry.group, amc_entry.index, 0, code)

    def _get_entries(self, data) -> List[AmcEntry]:
        api = self.coordinator.api
        ids = data["ids"]
        entries: List[AmcEntry] = [api.raw_entities[i] for i in ids if i in api.raw_entities]
        return entries

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        api = self.coordinator.api
        if not api.armed_any:
            return AlarmControlPanelState.DISARMED
        
        #only if all specified entities are armed show the mapped state
        for data in self._feature_data.values():
            entries = self._get_entries(data)
            if all(e.arm_state == AmcAlarmState.Armed for e in entries):
                return data["armed_state"]
        
        # mappa posizione -> valore
        order_map = {state: idx for idx, state in enumerate(AmcAlarmState)}
        
        state = AmcStatesParser(api.raw_states())
        groups = state.groups(api._central_id).list
        areas = state.areas(api._central_id).list
        # estrai solo gli arm_state e ordina in desc
        arm_states = [e.arm_state for e in [*groups, *areas] if e.arm_state != AmcAlarmState.Disarmed]
        if len(arm_states) > 0:
            top_state = max(arm_states, key=lambda s: order_map[s], default=None)  # opzionale, evita errore se arm_states è vuoto
            ha_state = amc_alarm_state_to_ha_state(top_state, AlarmControlPanelState.ARMED_CUSTOM_BYPASS)
            return ha_state
        return AlarmControlPanelState.DISARMED



class AmcEntryAlarmEntity(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    @property
    def code_format(self) -> CodeFormat | None:
        if not self.coordinator.api.pin_required:
            return None
        if self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True) and self.coordinator.get_config(CONF_ACP_DISARM_WITHOUT_PIN, True):
            return None
        if self.alarm_state == AlarmControlPanelState.PENDING and self._amc_entry.group != CentralDataSections.ZONES:
            return None
        # PIN obbligatorio numerico
        return CodeFormat.NUMBER
    
    @property
    def code_arm_required(self) -> bool:
        """Se True, il codice serve anche per ARM."""        
        if self.coordinator.api.pin_required and not self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True):
            return True
        return False

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        api = self.coordinator.api
        if not code and self.coordinator.api.pin_required and self.coordinator.get_config(CONF_ACP_ARM_WITHOUT_PIN, True):
            code = self.coordinator.get_default_pin()
        await api.command_set_states(self._amc_entry.group, self._amc_entry.index, 1, code)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        api = self.coordinator.api
        if not code and self.coordinator.api.pin_required and self.coordinator.get_config(CONF_ACP_DISARM_WITHOUT_PIN, True):
            code = self.coordinator.get_default_pin()
        if not code and self.coordinator.api.pin_required and self.alarm_state == AlarmControlPanelState.PENDING and self._amc_entry.group != CentralDataSections.ZONES:
            code = self.coordinator.get_default_pin()
        await api.command_set_states(self._amc_entry.group, self._amc_entry.index, 0, code)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        
        if self._amc_entry.group == CentralDataSections.ZONES:
            #for zones, can't use a pending state, pending indicate that zone is enabled for next activation
            state = amc_alarm_state_to_ha_state(self._amc_entry.arm_state, AlarmControlPanelState.ARMED_AWAY, AlarmControlPanelState.ARMED_AWAY)
            if state == AlarmControlPanelState.DISARMED and self._amc_entry.states.bit_on == 1:
                return AlarmControlPanelState.PENDING
            return state
            
        state = amc_alarm_state_to_ha_state(self._amc_entry.arm_state, AlarmControlPanelState.ARMED_AWAY)
        return state


        



def amc_alarm_state_to_ha_state(
    state: AmcAlarmState, 
    armed_state: AlarmControlPanelState = AlarmControlPanelState.ARMED_AWAY, 
    arming_state: AlarmControlPanelState = AlarmControlPanelState.PENDING
) -> AlarmControlPanelState:
    mapping = {
        AmcAlarmState.Disarmed: AlarmControlPanelState.DISARMED,
        AmcAlarmState.Arming: arming_state,
        AmcAlarmState.ArmingWithProblem: arming_state,
        AmcAlarmState.Armed: armed_state,  # usa il parametro opzionale
        AmcAlarmState.Triggered: AlarmControlPanelState.TRIGGERED,
    }
    return mapping.get(state, AlarmControlPanelState.DISARMED)