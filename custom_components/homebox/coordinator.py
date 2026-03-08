"""Data update coordinator for HomeBox."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HomeBoxApiClient,
    HomeBoxApiError,
    HomeBoxAuthenticationError,
    HomeBoxConnectionError,
)
from .const import DEFAULT_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type HomeBoxConfigEntry = ConfigEntry[HomeBoxDataUpdateCoordinator]


@dataclass(slots=True, frozen=True)
class HomeBoxStatistics:
    """HomeBox statistics used by sensor entities."""

    total_items: int
    total_locations: int
    total_value: float


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
            data = await self.api.async_get_group_statistics()
            return HomeBoxStatistics(
                total_items=data["total_items"],
                total_locations=data["total_locations"],
                total_value=data["total_value"],
            )
        except HomeBoxAuthenticationError:
            try:
                await self.api.async_authenticate(self._username, self._password)
                data = await self.api.async_get_group_statistics()
                return HomeBoxStatistics(
                    total_items=data["total_items"],
                    total_locations=data["total_locations"],
                    total_value=data["total_value"],
                )
            except HomeBoxConnectionError as err:
                raise UpdateFailed("Error communicating with HomeBox API") from err
            except HomeBoxApiError as err:
                raise UpdateFailed(f"Unexpected HomeBox API response: {err}") from err
        except HomeBoxConnectionError as err:
            raise UpdateFailed("Error communicating with HomeBox API") from err
        except HomeBoxApiError as err:
            raise UpdateFailed(f"Unexpected HomeBox API response: {err}") from err
