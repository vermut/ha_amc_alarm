from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .const import *

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    messages = {
        key: msg.as_dict()
        for key, msg in coordinator.api._messages.items()
    }
    config = coordinator.amcconfig.copy()
    #config[CONF_CENTRAL_USERNAME] = "***"
    #config[CONF_CENTRAL_PASSWORD] = "***"
    #config[CONF_EMAIL] = "***"
    #config[CONF_PASSWORD] = "***"
    return {        
        "entry_id": entry.entry_id,
        "config": config,
        "raw_states": coordinator.api.raw_states_json_model,
        "messages": messages,
    }