from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    messages = {
        key: msg.as_dict()
        for key, msg in coordinator.api._messages.items()
    }
    return {
        "entry_id": entry.entry_id,
        "raw_states": coordinator.api.raw_states_json_model,
        "messages": messages,
    }