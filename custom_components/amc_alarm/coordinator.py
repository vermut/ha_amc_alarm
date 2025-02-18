"""AMC alarm integration."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, SERVICE_RELOAD, CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.typing import ConfigType
from .amc_alarm_api import SimplifiedAmcApi
from .amc_alarm_api.exceptions import AuthenticationFailed, AmcException

from .const import *

_LOGGER = logging.getLogger(__name__)


class AmcDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    api: SimplifiedAmcApi | None = None
    amcconfig: ConfigType | None = None
    #_timeout: float = DEFAULT_TIMEOUT
    entity_unique_id_prefix = ""
    _first_update_data_executed = False
    _platforms_registered = False
    _last_devices_hash = ""
    _callback_disabled = False
    _async_request_refresh_from_callback = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.amcconfig = (entry.data or {}).copy()
        #self.amcconfig.update(entry.options or {})
        
        #_LOGGER.debug("AMC settings: %s" % self.amcconfig)

        self._callback_disabled = True
        #self.devices_for_platform = {}
        if entry:
            self.entity_unique_id_prefix = entry.unique_id or ""
        #timeout = get_config(CONF_TIMEOUT) or DEFAULT_TIMEOUT
        #if timeout > 0:
        #    self._timeout = float(timeout)
        uptade_interval = float(self.get_config(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
        if uptade_interval < 1:
            uptade_interval = DEFAULT_SCAN_INTERVAL
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=uptade_interval))

        userinfo = self.amcconfig
        self.api = SimplifiedAmcApi(
            userinfo[CONF_EMAIL],
            userinfo[CONF_PASSWORD],
            userinfo[CONF_CENTRAL_ID],
            userinfo[CONF_CENTRAL_USERNAME],
            userinfo[CONF_CENTRAL_PASSWORD],
            self.api_new_data_received_callback,
        )

    def get_config(self, key):
        if key in self.amcconfig:
            return self.amcconfig[key]
        return None

    async def api_new_data_received_callback(self):
        if self._callback_disabled:
            return
        _LOGGER.debug("api_new_data_received_callback: eseguo coordinator.async_request_refresh dopo update dei valori")
        states = self.api.raw_states()
        #if states and not states[api._central_id].returned:
        #    states[api._central_id].returned = 1
        self.async_set_updated_data(states)
        #states_to_return = states
        self._async_request_refresh_from_callback = True
        await self.async_request_refresh()
        self._async_request_refresh_from_callback = False

    async def _async_update_data(self):
        api = self.api
        states = api.raw_states()
        if self._async_request_refresh_from_callback and states:
            states[api._central_id].returned = 1
            self._async_request_refresh_from_callback = False
            return states
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        #if states_to_return:
        #    states = states_to_return
        #    states_to_return = None
        #    return states
        #if states:
        #    states[api._central_id].returned = 1
        if not states or states[api._central_id].returned:
            _LOGGER.debug("Updating coordinator..")
            self._callback_disabled = True
            await self.validate_amc_credentials()
            try:
                #await api.connect_if_disconnected()
                await api.command_get_states()
            except Exception as error:
                self._callback_disabled = False
                _LOGGER.exception("Unexpected exception occurred in async_wait_for_states: %s" % error)
                api.disconnect()
                raise UpdateFailed()
            
            for _ in range(900):
                states = api.raw_states()
                if states and not states[api._central_id].returned:
                    break
                await asyncio.sleep(0.1)
            
            self._callback_disabled = False

        if not states:
            raise UpdateFailed()
        states[api._central_id].returned = 1
        return states


    async def validate_amc_credentials(self) -> None:
        """Validate amc credential config."""            
        try:
            await self.api.connect_if_disconnected()
        except AuthenticationFailed as ex:
            raise ConfigEntryAuthFailed(ex) from ex
        except AmcException as ex:
            raise ConfigEntryNotReady("Unable to connect to AMC") from ex


    def get_user_pin(self, userPIN: str) -> str:
        if not userPIN:
            userPIN = self.get_config(CONF_USER_PIN)
        
        return userPIN

    def central_ids(self) -> list[str]:
        ids: list[str] = []
        if self.data and self.api._central_id and self.api._central_id in self.data:
            ids.append(self.api._central_id)
        return ids

    def get_id_prefix(self) -> str:
        return (self.get_config(CONF_TITLE) or "") + "_" + self.get_config(CONF_CENTRAL_ID) + "_"
