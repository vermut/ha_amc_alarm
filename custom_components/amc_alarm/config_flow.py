"""Config flow for Amc_alarm Integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from .amc_alarm_api import SimplifiedAmcApi
from .amc_alarm_api.exceptions import (
    ConnectionFailed,
    AmcException,
    AuthenticationFailed,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required("central_id"): str,
        vol.Required("central_username"): str,
        vol.Required("central_password"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        api = SimplifiedAmcApi(
            user_input[CONF_EMAIL],
            user_input[CONF_PASSWORD],
            user_input["central_id"],
            user_input["central_username"],
            user_input["central_password"],
        )

        try:
            await api.connect()
            for _ in range(10):
                if api.states:
                    break
                await asyncio.sleep(1)
        except ConnectionFailed:
            errors["base"] = "cannot_connect"
        except AuthenticationFailed:
            errors["base"] = "invalid_auth"
        except AmcException as e:
            errors["base"] = str(e)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            if user_input["central_id"] not in api.states:
                errors["base"] = "User login is fine but can't find AMC Central"
            else:
                return self.async_create_entry(
                    title="AMC %s" % user_input["central_id"], data=user_input
                )
        finally:
            await api.disconnect()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
