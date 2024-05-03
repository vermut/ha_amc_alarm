from __future__ import annotations

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
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
from . import AmcStatesType
from .const import DOMAIN
from .entity import AmcBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator[AmcStatesType] = hass.data[DOMAIN][
        entry.entry_id
    ]
    alarms: list[ArmZoneGroup] = []

    data = coordinator.data
    data: AmcStatesType
    for central_id, central in data.items():
        for amc_type in ["AREAS", "GROUPS"]:
            for zone in central[amc_type]:
                alarms.append(
                    ArmZoneGroup(
                        coordinator=coordinator,
                        name=zone.name,
                        entry_id=entry.entry_id,
                        central_id=central_id,
                        amc_id=zone.Id,
                        amc_type=amc_type,
                    )
                )

    async_add_entities(alarms, True)


class ArmZoneGroup(AmcBaseEntity, AlarmControlPanelEntity):
    _attr_supported_features = (
        # TODO changes AlarmControlPanelEntityFeature.ARM_AWAY
    )

    @property
    def state(self) -> str | None:
        if self._amc_entry.states.anomaly:  # TODO verify that it is correct flag
            return STATE_ALARM_TRIGGERED

        if self._amc_entry.states.bit_armed:
            return STATE_ALARM_ARMED_AWAY

        return STATE_ALARM_DISARMED
