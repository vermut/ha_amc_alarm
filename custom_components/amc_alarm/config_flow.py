"""Config flow for Amc_alarm Integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
from homeassistant.util import slugify
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)


from .amc_alarm_api import SimplifiedAmcApi
from .amc_alarm_api.api import AmcStatesParser
from .amc_alarm_api.exceptions import * 
#(
#    ConnectionFailed,
#    AmcException,
#    AuthenticationFailed,
#)
from .const import *

_LOGGER = logging.getLogger(__name__)


class AmcConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = CONF_CURR_VERSION
    _entry_data: dict[str, Any] | None = None
    user_input: dict[str, Any] | None = None
    api: SimplifiedAmcApi| None = None

    def _init_step(self, user_input, schema):
        #su una nuova maschera salvo i dati della precedente
        if user_input is None and self.user_input is not None:
            self._save_user_input()
        self.schema = schema
        self.schema_vol = vol.Schema(schema)
        self.errors: dict[str, str] = {}
        self.user_input = user_input
        self._entry_data = self._entry_data or {}
        self._entry_data_with_user_input = self._entry_data.copy()
        if user_input:
            self._dict_update_with_user_input(self._entry_data_with_user_input)
        self.schema_vol = self.add_suggested_values_to_schema(
            vol.Schema(self.schema),
            self._entry_data_with_user_input,
        )

    def _save_user_input(self):
        self._dict_update_with_user_input(self._entry_data)

    def _dict_update_with_user_input(self, options):
        for key in self.schema.keys():  # set all values in form, if not present remove it
            value = self.user_input.get(str(key))
            options[str(key)] = value
            if value is None:  # remove None value, problem to save in json!
                options.pop(str(key))

    def _async_show_form_step(self, step):
        return self.async_show_form(step_id=step, data_schema=self.schema_vol, errors=self.errors)

    def _async_save_options(self):
        self._save_user_input()
        self._entry_data[CONF_FLOW_VERSION] = CONF_FLOW_LAST_VERSION
        #self.hass.config_entries.async_update_entry(self.config_entry, data=self._entry_data)
        # return self.async_create_entry(title="", data=self._entry_data)
        #return self.async_create_entry(title="", data={})
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=self._entry_data
            )
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data=self._entry_data,
            )
        title = "AMC %s %s" % (self._entry_data[CONF_TITLE], self._entry_data[CONF_CENTRAL_ID])
        return self.async_create_entry(title=title, data=self._entry_data)

    #@staticmethod
    #@callback
    #def async_get_options_flow(config_entry):
    #    """Get options flow for this handler."""
    #    return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        self._init_step(user_input, get_schema_config_user(user_input))
        
        if user_input is not None:
            api = SimplifiedAmcApi(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                user_input[CONF_CENTRAL_ID],
                user_input[CONF_CENTRAL_USERNAME],
                user_input[CONF_CENTRAL_PASSWORD],
            )
            self.api = api
            errors=self.errors
            try:
                await api.command_get_states_and_return()
            #except ConnectionFailed:
            #    errors["base"] = "cannot_connect"
            #except AuthenticationFailed:
            #    errors["base"] = "invalid_auth"
            #except AmcCentralNotFoundException:
            #    errors["base"] = "User login is fine but can't find AMC Central"
            except AmcException as e:
                errors["base"] = str(e)
            except ConnectionFailed as e:
                errors["base"] = str(e)
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = str(e)
            else:
                states = AmcStatesParser(api.raw_states())
                centralId = user_input[CONF_CENTRAL_ID]
                central : AmcCentralResponse = states.raw_states().get(centralId)
                #userPin = user_input.get(CONF_USER_PIN)
                #if not central:
                #    errors["base"] = "User login is fine but can't find AMC Central."
                #if userPin and not self.errors: # only for amcProtoVer >= 2
                #    #_LOGGER.debug("User pin: %s - %s" % (userPin, str(len(userPin))))
                #    if states.users(centralId) is None:
                #        errors["base"] = "User PIN not allowed, users not received"
                #    elif not states.user_by_pin(centralId, userPin):
                #        errors["base"] = "User PIN not valid"

                if not self.errors:
                    unique_id = slugify("AMC %s" % (centralId))
                    await self.async_set_unique_id(unique_id)
                    if self.source in {SOURCE_REAUTH, SOURCE_RECONFIGURE}:
                        self._abort_if_unique_id_mismatch(reason="account_mismatch")
                    else:
                        self._abort_if_unique_id_configured()

                    if not CONF_TITLE in self._entry_data:
                        realname = states.real_name(centralId)
                        self._entry_data[CONF_TITLE] = realname

                    return await self.async_step_two()

            finally:
                await api.disconnect()

        return self._async_show_form_step("user")

    # https://gitlab.nbcc.mobi/Sandy.Liu/HAcore-SL/-/blob/dev/homeassistant/components/bmw_connected_drive/config_flow.py
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle configuration by re-auth."""        
        self._entry_data = {}
        self._entry_data.update(entry_data)
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reconfiguration flow initialized by the user."""
        self._entry_data = {}
        self._entry_data.update(self._get_reconfigure_entry().data)
        return await self.async_step_user()

    async def async_step_two(self, user_input=None):
        """Manage domain and entity filters."""
        self._init_step(user_input, get_schema_options_two(user_input or self._entry_data, self.api))

        if user_input is not None and not self.errors:
            return await self.async_step_three()

        return self._async_show_form_step("two")

    async def async_step_three(self, user_input=None):
        """Manage domain and entity filters."""
        self._init_step(user_input, get_schema_options_three(user_input or self._entry_data, self.api))

        if user_input is not None and not self.errors:
            return self._async_save_options()

        return self._async_show_form_step("three")




def get_schema_config_user(config: dict = {}) -> dict:
    """Return a shcema configuration dict for HACS."""
    config = config if CONF_CENTRAL_USERNAME in (config or {}) else None
    schema = {
        #vol.Required(CONF_TITLE, description=get_vol_descr(config, CONF_TITLE)): str,
        vol.Required(CONF_EMAIL, description=get_vol_descr(config, CONF_EMAIL)): str,
        vol.Required(CONF_PASSWORD, description=get_vol_descr(config, CONF_PASSWORD)): str,
        vol.Required(CONF_CENTRAL_ID, description=get_vol_descr(config, CONF_CENTRAL_ID)): str,
        vol.Required(CONF_CENTRAL_USERNAME, description=get_vol_descr(config, CONF_CENTRAL_USERNAME)): str,
        vol.Required(CONF_CENTRAL_PASSWORD, description=get_vol_descr(config, CONF_CENTRAL_PASSWORD)): str,
    }
    return schema


def get_schema_options_init(config: dict = {}) -> dict:
    """Return a shcema configuration dict for HACS."""
    schema = get_schema_config_user(config=config)
    #schema.pop(CONF_TITLE, "")
    #schema.pop(CONF_CENTRAL_ID, "")
    return schema


def get_schema_options_two(config: dict = {}, api: SimplifiedAmcApi = None) -> dict:
    """Return a shcema configuration dict for HACS."""
    config = config or {}
    config = config if CONF_STATUS_ZONE_PREFIX in config else None
    #if config and not config.get(CONF_SCAN_INTERVAL):
    #    config[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
    #domains = sorted(PLATFORMS)
    schema = {
        vol.Required(CONF_TITLE, description=get_vol_descr(config, CONF_TITLE)): str,

        vol.Required(CONF_SCAN_INTERVAL, description=get_vol_descr(config, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): int,
    }
    
    if api.pin_required:
        states = AmcStatesParser(api.raw_states())
        users = states.users(api._central_id)
        OPTIONS = {
            "-1": "Disable setters"
        }        
        OPTIONS.update({
            v.index: v.name.strip()
            for k, v in users.items()
            if k.strip().isdigit()  # tiene solo le chiavi composte da cifre
        })
        default = ""
        if config and CONF_USER_PIN in config:
            user = states.user_by_pin(api._central_id, config.get(CONF_USER_PIN))
            if user:
                default = user.index
        schema.update({
            vol.Required(CONF_USER_INDEX, description=get_vol_descr(config, CONF_USER_INDEX, default)): vol.In(OPTIONS)
        })

    schema.update({
        vol.Optional(CONF_STATUS_SYSTEM_PREFIX, description=get_vol_descr(config, CONF_STATUS_SYSTEM_PREFIX, "Stato sistema")): str,
        
        vol.Optional(CONF_STATUS_GROUP_INCLUDED, description=get_vol_descr(config, CONF_STATUS_GROUP_INCLUDED, False)): bool,
        vol.Optional(CONF_STATUS_GROUP_PREFIX, description=get_vol_descr(config, CONF_STATUS_GROUP_PREFIX, "Stato gruppo")): str,
        vol.Optional(CONF_STATUS_AREA_INCLUDED, description=get_vol_descr(config, CONF_STATUS_AREA_INCLUDED, False)): bool,
        vol.Optional(CONF_STATUS_AREA_PREFIX, description=get_vol_descr(config, CONF_STATUS_AREA_PREFIX, "Stato area")): str,
        vol.Optional(CONF_STATUS_ZONE_INCLUDED, description=get_vol_descr(config, CONF_STATUS_ZONE_INCLUDED, True)): bool,
        vol.Optional(CONF_STATUS_ZONE_PREFIX, description=get_vol_descr(config, CONF_STATUS_ZONE_PREFIX, "Stato zona")): str,
        
        vol.Optional(CONF_OUTPUT_INCLUDED, description=get_vol_descr(config, CONF_OUTPUT_INCLUDED, True)): bool,
        vol.Optional(CONF_OUTPUT_PREFIX, description=get_vol_descr(config, CONF_OUTPUT_PREFIX, "Uscita")): str,
    })

    return schema


def get_schema_options_three(config: dict = {}, api: SimplifiedAmcApi = None) -> dict:
    """Return a shcema configuration dict for HACS."""
    config = config or {}
    config = config if CONF_ACP_ZONE_PREFIX in config else None
    schema = { }
    if api.pin_required:        
        schema.update({
            vol.Optional(CONF_ACP_ARM_WITHOUT_PIN, description=get_vol_descr(config, CONF_ACP_ARM_WITHOUT_PIN, True)): bool,
            vol.Optional(CONF_ACP_DISARM_WITHOUT_PIN, description=get_vol_descr(config, CONF_ACP_DISARM_WITHOUT_PIN, True)): bool,
        })
    schema.update({
        vol.Optional(CONF_ACP_GROUP_INCLUDED, description=get_vol_descr(config, CONF_ACP_GROUP_INCLUDED, True)): bool,
        vol.Optional(CONF_ACP_GROUP_PREFIX, description=get_vol_descr(config, CONF_ACP_GROUP_PREFIX, "Gruppo")): str,
        vol.Optional(CONF_ACP_AREA_INCLUDED, description=get_vol_descr(config, CONF_ACP_AREA_INCLUDED, True)): bool,
        vol.Optional(CONF_ACP_AREA_PREFIX, description=get_vol_descr(config, CONF_ACP_AREA_PREFIX, "Area")): str,
        vol.Optional(CONF_ACP_ZONE_INCLUDED, description=get_vol_descr(config, CONF_ACP_ZONE_INCLUDED, True)): bool,
        vol.Optional(CONF_ACP_ZONE_PREFIX, description=get_vol_descr(config, CONF_ACP_ZONE_PREFIX, "Zona")): str,        
    })
    return schema



def get_vol_default(config: dict, key, default=None):
    if config:
        return config.get(key) or vol.UNDEFINED
    return default or vol.UNDEFINED


def get_vol_descr(config: dict, key, default=None):
    def_value = get_vol_default(config, key, default)
    res = ({"suggested_value": def_value}) if def_value and def_value is not vol.UNDEFINED else {}
    return res

