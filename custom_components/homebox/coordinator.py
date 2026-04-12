"""Data update coordinator for HomeBox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    HomeBoxApiClient,
    HomeBoxApiError,
    HomeBoxAuthenticationError,
    HomeBoxConnectionError,
)
from .battery_forecast import (
    LinkedBatteryForecast,
    async_collect_linked_battery_forecasts,
)
from .const import DEFAULT_POLL_INTERVAL, DOMAIN
from .linking import (
    HomeBoxTaggedItem,
    async_sync_ha_areas_to_hb_locations,
    scan_tagged_items_for_links,
)
from .maintenance import async_sync_battery_maintenance_items
from .models import HomeBoxGroupStatistics

_LOGGER = logging.getLogger(__name__)

type HomeBoxConfigEntry = ConfigEntry[HomeBoxDataUpdateCoordinator]


@dataclass(slots=True, frozen=True)
class HomeBoxStatistics:
    """HomeBox statistics used by sensor entities."""

    total_items: int
    total_locations: int
    total_value: float
    unlinked_hb_items: list[HomeBoxTaggedItem]
    link_conflicts: list[str]
    linked_battery_forecasts: dict[str, LinkedBatteryForecast]
    maintenance_due_today: int
    maintenance_due_next_week: int


class HomeBoxDataUpdateCoordinator(DataUpdateCoordinator[HomeBoxStatistics]):
    """Class to manage fetching HomeBox data."""

    def __init__(
        self, hass: HomeAssistant, api: HomeBoxApiClient, entry: HomeBoxConfigEntry
    ) -> None:
        """Initialize the HomeBox coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_POLL_INTERVAL,
        )
        self.api = api
        self._username = entry.data[CONF_USERNAME]
        self._password = entry.data[CONF_PASSWORD]

    async def _async_update_data(self) -> HomeBoxStatistics:
        """Fetch HomeBox statistics."""
        try:
            if not self.api.is_authenticated:
                await self.api.async_authenticate(self._username, self._password)
            return await self._async_fetch_statistics_and_links()
        except HomeBoxAuthenticationError:
            try:
                await self.api.async_authenticate(self._username, self._password)
                return await self._async_fetch_statistics_and_links()
            except HomeBoxConnectionError as err:
                raise UpdateFailed("Error communicating with HomeBox API") from err
            except HomeBoxApiError as err:
                raise UpdateFailed(f"Unexpected HomeBox API response: {err}") from err
        except HomeBoxConnectionError as err:
            raise UpdateFailed("Error communicating with HomeBox API") from err
        except HomeBoxApiError as err:
            raise UpdateFailed(f"Unexpected HomeBox API response: {err}") from err

    async def _async_fetch_statistics_and_links(self) -> HomeBoxStatistics:
        """Fetch statistics plus link scan data with shared logic."""
        await self._async_sync_ha_areas()
        group_stats: HomeBoxGroupStatistics = await self.api.async_get_group_statistics()
        link_scan = await scan_tagged_items_for_links(self.hass, self.api, self.config_entry)
        if link_scan.updated_options is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=link_scan.updated_options
            )
        maintenance_due_today, maintenance_due_next_week = (
            await self._async_count_maintenance_due()
        )
        battery_forecasts = await async_collect_linked_battery_forecasts(
            self.hass, self.config_entry
        )
        try:
            new_options = await async_sync_battery_maintenance_items(
                self.hass, self.config_entry, self.api, battery_forecasts
            )
        except (HomeBoxApiError, HomeBoxAuthenticationError, HomeBoxConnectionError):
            _LOGGER.exception(
                "Unable to sync Home Assistant maintenance items for battery forecasts"
            )
        else:
            if new_options is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options=new_options
                )
        self._async_create_linking_discovery_flows(link_scan.unlinked_hb_items)
        return HomeBoxStatistics(
            total_items=group_stats.total_items,
            total_locations=group_stats.total_locations,
            total_value=group_stats.total_value,
            unlinked_hb_items=link_scan.unlinked_hb_items,
            link_conflicts=link_scan.conflicts,
            linked_battery_forecasts=battery_forecasts,
            maintenance_due_today=maintenance_due_today,
            maintenance_due_next_week=maintenance_due_next_week,
        )

    async def _async_count_maintenance_due(self) -> tuple[int, int]:
        """Count scheduled HomeBox maintenance entries due today and in the next 7 days."""
        today = dt_util.utcnow().date()
        due_until = today + timedelta(days=7)
        try:
            entries = await self.api.async_get_hb_maintenance(status="scheduled")
        except (HomeBoxApiError, HomeBoxConnectionError, HomeBoxAuthenticationError):
            _LOGGER.warning(
                "Unable to query HomeBox maintenance entries for due-next-week count"
            )
            return 0, 0

        due_today_count = 0
        due_next_week_count = 0
        for entry in entries:
            raw_scheduled_date = entry.get("scheduledDate")
            if not isinstance(raw_scheduled_date, str) or not raw_scheduled_date.strip():
                continue

            scheduled_dt = dt_util.parse_datetime(raw_scheduled_date)
            if scheduled_dt is not None:
                scheduled_date = scheduled_dt.date()
            else:
                scheduled_date = dt_util.parse_date(raw_scheduled_date)
                if scheduled_date is None:
                    continue

            if scheduled_date == today:
                due_today_count += 1
            if today < scheduled_date <= due_until:
                due_next_week_count += 1
        return due_today_count, due_next_week_count

    async def _async_sync_ha_areas(self) -> None:
        """Mirror Home Assistant areas into HomeBox locations."""
        try:
            await async_sync_ha_areas_to_hb_locations(self.hass, self.api)
        except (HomeBoxApiError, HomeBoxConnectionError, HomeBoxAuthenticationError):
            _LOGGER.warning(
                "Unable to sync Home Assistant areas to HomeBox locations during refresh"
            )

    def _async_create_linking_discovery_flows(
        self, unlinked_hb_items: list[HomeBoxTaggedItem]
    ) -> None:
        """Create integration-discovery prompts for unlinked HomeBox items."""
        for hb_item in unlinked_hb_items:
            discovery_flow.async_create_flow(
                self.hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    "config_entry_id": self.config_entry.entry_id,
                    "hb_item_id": hb_item.hb_item_id,
                    "hb_item_name": hb_item.name,
                },
            )
