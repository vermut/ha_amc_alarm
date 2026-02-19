from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from .const import *
from pydantic import BaseModel

import json

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    api = coordinator.api
    config = coordinator.amcconfig.copy()
    data = {        
        "entry_id": entry.entry_id,
        "config": config
    }
    data.update(api._get_status_info_dict())
    data.update({        
        "raw_states": api.raw_states_json_model,
        "messages": api._messages,
    })
    sensitive_values = { CONF_CENTRAL_ID, CONF_CENTRAL_USERNAME, CONF_CENTRAL_PASSWORD, CONF_EMAIL, CONF_PASSWORD };
    json_str = json.dumps(serialize(data), default=str)
    for key in sensitive_values:
        val = config[key]
        json_str = json_str.replace(val, "***" + key + "***" )
    masked_data = json.loads(json_str)
    return masked_data

def serialize(obj):
    from datetime import datetime
    """Convert dict, list, BaseModel into JSON-serializable structures."""
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize(v) for v in obj]
    elif hasattr(obj, "dict"):
        return serialize(obj.dict())  # ricorsivo anche dentro BaseModel annidati
    elif isinstance(obj, datetime):
        return obj.isoformat()  # datetime → string ISO
    else:
        return obj