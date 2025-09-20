"""AMC alarm integration."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, SERVICE_RELOAD, CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ConfigEntryError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.typing import ConfigType
from .amc_alarm_api import SimplifiedAmcApi
from .amc_alarm_api.api import AmcStatesParser, ConnectionState
from .amc_alarm_api.exceptions import * # AuthenticationFailed, AmcException
from .amc_alarm_api.amc_proto import AmcCommands
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
        self._device_info = None  # sarà creato solo la prima volta
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
        #self.api.set_task_factory(
        #    create_task=hass.async_create_task,
        #    create_future=hass.loop.create_future
        #)
        
    @property
    def data_parsed(self) -> AmcStatesParser:
        return AmcStatesParser(self.data)

    @property
    def device_available(self):
        return self.api._ws_state == ConnectionState.CENTRAL_OK

    @property
    def device_info(self) -> DeviceInfo:
        if self._device_info is None:
            central_id = self.get_config(CONF_CENTRAL_ID)
            device_title = self.get_config(CONF_TITLE)
            if not self.api._raw_states_central_valid:
                return DeviceInfo(
                    identifiers={(DOMAIN, central_id)},
                    manufacturer="AMC Elettronica",
                    name=device_title or central_id
                )
            states = AmcStatesParser(self.data)
            # Creo DeviceInfo solo la prima volta
            self._device_info = DeviceInfo(
                identifiers={(DOMAIN, central_id)},
                manufacturer="AMC Elettronica",
                model=states.real_name(central_id) + " " + states.model(central_id),
                name=device_title or states.real_name(central_id),
                sw_version=states.version(central_id),
                serial_number=central_id
            )
        return self._device_info

    def get_config(self, key):
        if key in self.amcconfig:
            return self.amcconfig[key]
        return None

    async def api_new_data_received_callback(self):
        if self._callback_disabled:
            return
        #already logged as Manually updated amc_alarm data
        #_LOGGER.debug("api_new_data_received_callback: eseguo coordinator.async_request_refresh dopo update dei valori")
        states = self.api.raw_states() or {}
        self.async_set_updated_data(states)
        self._async_request_refresh_from_callback = True
        await self.async_request_refresh()
        #self._async_request_refresh_from_callback = False

    async def _async_update_data(self):
        api = self.api
        states = api.raw_states()
        
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """    
        try:
            if not api._raw_states_central_valid:
                _LOGGER.debug("Updating coordinator..")
                self._callback_disabled = True
                states = await api.command_get_states_and_return()
            elif self._async_request_refresh_from_callback:
                self._async_request_refresh_from_callback = False
                states = states or {}
            else:
                api._msg_quee_get_states = True
                await self.api._send_msg_quee()
            if api._ws_state == ConnectionState.STOPPED and api._ws_state_stop_exeception:
                raise api._ws_state_stop_exeception
        except AuthenticationFailed as ex:
            raise ConfigEntryAuthFailed(ex) from ex
        except AmcCentralNotFoundException as ex:
            raise ConfigEntryAuthFailed(ex) from ex
        except AmcException as ex:
            raise ConfigEntryNotReady(ex) from ex
        except Exception as error:
            _LOGGER.exception("Unexpected exception occurred in async_wait_for_states: %s" % error)
            raise UpdateFailed(error)
        finally:
            self._callback_disabled = False

        if not states:
            raise UpdateFailed()
        return states

    def get_user_pin(self, userPIN: str) -> str:
        if not userPIN:
            userIdx = self.get_config(CONF_USER_INDEX)
            if userIdx > -1:
                userPIN = self.data_parsed.user_pin_by_index(self.api._central_id, userIdx)
        return userPIN

    def central_ids(self) -> list[str]:
        ids: list[str] = []
        if self.data and self.api._central_id and self.api._central_id in self.data:
            ids.append(self.api._central_id)
        return ids

    def get_id_prefix(self) -> str:
        return (self.get_config(CONF_TITLE) or "") + "_" + self.get_config(CONF_CENTRAL_ID) + "_"
