"""AMC alarm integration."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .amc_alarm_api import SimplifiedAmcApi
from .amc_alarm_api.exceptions import AuthenticationFailed, AmcException
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})

    async def api_new_data_received_callback():
        await coordinator.async_request_refresh()

    api = SimplifiedAmcApi(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data["central_id"],
        entry.data["central_username"],
        entry.data["central_password"],
        api_new_data_received_callback,
    )

    try:
        await api.connect()
    except AuthenticationFailed as ex:
        raise ConfigEntryAuthFailed(ex) from ex
    except AmcException as ex:
        raise ConfigEntryNotReady("Unable to connect to AMC") from ex

    async def async_wait_for_states():
        try:
            await api.connect_if_disconnected()
            await api.command_get_states()
        except Exception as error:
            _LOGGER.exception("Unexpected exception occurred: %s", error)
            raise UpdateFailed()
            
        for _ in range(30):
            if api.raw_states():
                break
            await asyncio.sleep(1)

        if not api.raw_states():
            raise UpdateFailed()

        return api.raw_states()

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_wait_for_states,
        update_interval=timedelta(minutes=5),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN]["__api__"] = api
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.data[DOMAIN]["__api__"].disconnect()
        hass.data[DOMAIN].pop("__api__")
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
