"""The HomeBox integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HomeBoxApiClient,
    HomeBoxApiError,
    HomeBoxAuthenticationError,
    HomeBoxConnectionError,
)
from .const import CONF_HA_DEVICE_TO_HB_ITEM, CONF_HB_ITEM_TO_HA_DEVICE, CONF_LINKS
from .coordinator import HomeBoxConfigEntry, HomeBoxDataUpdateCoordinator
from .linking import (
    async_cleanup_removed_ha_device_link,
    async_sync_all_linked_hb_item_locations,
    async_sync_linked_hb_item_location,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: HomeBoxConfigEntry) -> bool:
    """Set up HomeBox from a config entry."""
    if CONF_LINKS not in entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_LINKS: {
                    CONF_HA_DEVICE_TO_HB_ITEM: {},
                    CONF_HB_ITEM_TO_HA_DEVICE: {},
                },
            },
        )

    api = HomeBoxApiClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = HomeBoxDataUpdateCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()
    try:
        await async_sync_all_linked_hb_item_locations(hass, entry, api)
    except (
        HomeBoxApiError,
        HomeBoxAuthenticationError,
        HomeBoxConnectionError,
    ):
        _LOGGER.warning(
            "Unable to sync linked HomeBox item locations during startup reconciliation"
        )

    async def _async_sync_location_for_device(ha_device_id: str) -> None:
        """Sync HomeBox item location for a linked Home Assistant device."""
        try:
            await async_sync_linked_hb_item_location(hass, entry, api, ha_device_id)
        except (
            HomeBoxApiError,
            HomeBoxAuthenticationError,
            HomeBoxConnectionError,
        ):
            return

    @callback
    def _async_handle_device_registry_updated(
        event: Event[dr.EventDeviceRegistryUpdatedData],
    ) -> None:
        """Handle Home Assistant device updates."""
        action = event.data["action"]
        if action == "update":
            if "area_id" not in event.data["changes"]:
                return
            hass.async_create_task(
                _async_sync_location_for_device(event.data["device_id"])
            )
            return

        if action != "remove":
            return

        async def _async_cleanup_removed_device() -> None:
            current_entry = hass.config_entries.async_get_entry(entry.entry_id)
            if current_entry is None or current_entry.state is not ConfigEntryState.LOADED:
                return

            try:
                new_options = await async_cleanup_removed_ha_device_link(
                    hass, entry, api, event.data["device_id"]
                )
            except (
                HomeBoxApiError,
                HomeBoxAuthenticationError,
                HomeBoxConnectionError,
            ):
                _LOGGER.warning(
                    "Unable to clean up HomeBox link after HA device removal"
                )
                return

            if new_options is not None:
                current_entry = hass.config_entries.async_get_entry(entry.entry_id)
                if (
                    current_entry is not None
                    and current_entry.state is ConfigEntryState.LOADED
                ):
                    hass.config_entries.async_update_entry(
                        current_entry, options=new_options
                    )

        hass.async_create_task(_async_cleanup_removed_device())

    entry.async_on_unload(
        hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, _async_handle_device_registry_updated
        )
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeBoxConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: HomeBoxConfigEntry) -> None:
    """Reload entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)
