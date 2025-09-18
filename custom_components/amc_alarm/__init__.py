"""AMC alarm integration."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, SERVICE_RELOAD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ConfigEntryError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers import issue_registry as ir
from .coordinator import AmcDataUpdateCoordinator
from .const import *

_LOGGER = logging.getLogger(__name__)

# @ asyncio.coroutine
async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up from config."""
    hass.data.setdefault(DOMAIN, {})

    await add_services(hass)

    return True

# spunto da https://github.com/ludeeus/integration_blueprint/blob/main/custom_components/integration_blueprint/__init__.py

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    if entry.version < CONF_CURR_VERSION:
        _LOGGER.error("Error async_setup_entry config entry from version %s", entry.version)
        return False

    coordinator = AmcDataUpdateCoordinator(hass, entry=entry)

    #if coordinator.get_config(CONF_FLOW_VERSION) != CONF_FLOW_LAST_VERSION:
    #    raise ConfigEntryError("Please execute device options configuration...")

    entry.runtime_data = coordinator
    
    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    await coordinator.async_config_entry_first_refresh()

    # Set up all platforms for this device/entry.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    #hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    # Reload entry when its updated.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    #if not entry.entry_id in hass.data[DOMAIN]:
    #    return True
    coordinator: AmcDataUpdateCoordinator = entry.runtime_data
    if not coordinator:
        return True

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await coordinator.api.disconnect()
        entry.runtime_data = None

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    """Reload the config entry when it changed."""
    await hass.config_entries.async_reload(entry.entry_id)
    #await async_unload_entry(hass, entry)
    #await async_setup_entry(hass, entry)

async def add_services(hass: HomeAssistant):
    """Add services."""

    async def _handle_reload(service):
        entries_to_reload = hass.config_entries.async_entries(DOMAIN)
        for entry in entries_to_reload:
            await async_reload_entry(hass, entry)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
    )

async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Gestisce la migrazione dei config entries quando VERSION cambia."""
    _LOGGER.warning("Migrating config entry from version %s", entry.version)
##
    ### invece di migrare, avvia il wizard
    ##hass.async_create_task(
    ##    hass.config_entries.flow.async_init(
    ##        DOMAIN,
    ##        context={"source": "user"},
    ##        data={}
    ##    )
    ##)
    #return True

    if entry.version > CONF_CURR_VERSION:
        return False

    if entry.version == 1:
        new_data = {**entry.data}
        if CONF_USER_PIN in new_data:
            coordinator = AmcDataUpdateCoordinator(hass, entry=entry)            
            await coordinator.async_config_entry_first_refresh()
            await coordinator.api.disconnect()
            pin = new_data.pop(CONF_USER_PIN)
            user = coordinator.data_parsed.user_by_pin(coordinator.api._central_id, pin)
            new_data[CONF_USER_INDEX] = user.index
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migration to version %s successful", entry.version)
        #return True

    #hass.config_entries.async_update_entry(entry, data=new_data, version=CONF_CURR_VERSION)
    #_LOGGER.info("Migration to version %s successful", entry.version)

#    # se la versione non è gestita → errore
    return True