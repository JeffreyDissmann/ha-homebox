"""The HomeBox integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HomeBoxApiClient
from .coordinator import HomeBoxConfigEntry, HomeBoxDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HomeBoxConfigEntry) -> bool:
    """Set up HomeBox from a config entry."""
    api = HomeBoxApiClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = HomeBoxDataUpdateCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeBoxConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
