"""Config flow for the HomeBox integration."""

from __future__ import annotations

from difflib import SequenceMatcher
import logging
import re
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.network import normalize_url

from .api import (
    HomeBoxApiClient,
    HomeBoxApiError,
    HomeBoxAuthenticationError,
    HomeBoxConnectionError,
)
from .const import (
    CONF_AREA,
    CONF_HA_DEVICE_ID,
    CONF_HB_ITEM_ID,
    CONF_LINK_ACTION,
    DEFAULT_NAME,
    DOMAIN,
)
from .linking import (
    apply_link,
    async_cleanup_unlinked_hb_backlinks,
    list_link_rows,
    remove_link,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Optional(CONF_AREA): selector.AreaSelector(),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    api = HomeBoxApiClient(data[CONF_HOST], async_get_clientsession(hass))

    try:
        await api.async_authenticate(data[CONF_USERNAME], data[CONF_PASSWORD])
        await api.async_get_total_items()
    except HomeBoxAuthenticationError as err:
        raise InvalidAuth(str(err)) from err
    except HomeBoxConnectionError as err:
        raise CannotConnect from err

    normalized_host = normalize_host(data[CONF_HOST])
    return {
        "title": data.get(CONF_NAME) or URL(normalized_host).host or normalized_host
    }


def normalize_host(host: str) -> str:
    """Normalize host to absolute URL string."""
    host = host.strip()
    if "://" not in host:
        host = f"http://{host}"
    return normalize_url(host).rstrip("/")


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeBox."""

    VERSION = 1
    _auth_error_detail: str = "No authentication attempt yet."

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> HomeBoxOptionsFlow:
        """Get the options flow for this handler."""
        return HomeBoxOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        suggested_values: dict[str, Any] = {}
        if user_input is not None:
            normalized_host = normalize_host(user_input[CONF_HOST])
            try:
                user_input[CONF_HOST] = normalized_host
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth as err:
                errors["base"] = "invalid_auth_homebox"
                self._auth_error_detail = err.detail
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(normalized_host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)
            suggested_values = {
                CONF_HOST: user_input.get(CONF_HOST, ""),
                CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                CONF_NAME: user_input.get(CONF_NAME, DEFAULT_NAME),
                CONF_AREA: user_input.get(CONF_AREA, ""),
            }

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors,
            description_placeholders={"auth_error_detail": self._auth_error_detail},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

    def __init__(self, detail: str = "No details returned by HomeBox.") -> None:
        """Initialize InvalidAuth."""
        self.detail = detail


class HomeBoxOptionsFlow(OptionsFlowWithConfigEntry):
    """Options flow for HomeBox linking wizard."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__(config_entry)
        self._selected_hb_item_id: str | None = None
        self._selected_hb_item_name: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options for HomeBox."""
        coordinator = self.config_entry.runtime_data
        await coordinator.async_refresh()
        unlinked_count = len(coordinator.data.unlinked_hb_items)
        if unlinked_count:
            link_status = (
                f"{unlinked_count} tagged HomeBox item(s) are ready to be linked."
            )
        else:
            link_status = "No tagged HomeBox items are waiting for linking."

        return self.async_show_menu(
            step_id="init",
            menu_options=["link_ha_device", "unlink_ha_device", "resync"],
            description_placeholders={"link_status": link_status},
        )

    async def async_step_link_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select unlinked hb_item."""
        coordinator = self.config_entry.runtime_data
        await coordinator.async_refresh()
        unlinked_hb_items = coordinator.data.unlinked_hb_items
        if not unlinked_hb_items:
            return self.async_abort(reason="no_unlinked_hb_items")

        if user_input is not None:
            self._selected_hb_item_id = user_input[CONF_HB_ITEM_ID]
            selected_hb_item = next(
                (
                    hb_item
                    for hb_item in unlinked_hb_items
                    if hb_item.hb_item_id == self._selected_hb_item_id
                ),
                None,
            )
            self._selected_hb_item_name = (
                selected_hb_item.name if selected_hb_item else None
            )
            return await self.async_step_choose_ha_device()

        options = [
            selector.SelectOptionDict(
                value=hb_item.hb_item_id,
                label=f"{hb_item.name} ({hb_item.hb_item_id})",
            )
            for hb_item in unlinked_hb_items
        ]
        return self.async_show_form(
            step_id="link_ha_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HB_ITEM_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode="dropdown")
                    )
                }
            ),
        )

    async def async_step_choose_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select ha_device for hb_item and apply link."""
        if self._selected_hb_item_id is None:
            return self.async_abort(reason="missing_hb_item")

        device_registry = dr.async_get(self.hass)
        hb_item_name = self._selected_hb_item_name or ""
        ranked_devices = sorted(
            (
                (
                    _name_similarity(hb_item_name, device.name_by_user or device.name),
                    device,
                )
                for device in device_registry.devices.values()
                if device.name_by_user or device.name
            ),
            key=lambda result: result[0],
            reverse=True,
        )

        ha_device_options = [
            selector.SelectOptionDict(
                value=device.id,
                label=device.name_by_user or device.name or device.id,
            )
            for _, device in ranked_devices
        ]
        if not ha_device_options:
            return self.async_abort(reason="no_ha_devices")

        suggested_ha_device_id: str | None = None
        if ranked_devices and ranked_devices[0][0] >= 0.65:
            suggested_ha_device_id = ranked_devices[0][1].id

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_ha_device_id = user_input[CONF_HA_DEVICE_ID]
            coordinator = self.config_entry.runtime_data
            try:
                new_options = await apply_link(
                    self.hass,
                    self.config_entry,
                    coordinator.api,
                    selected_ha_device_id,
                    self._selected_hb_item_id,
                )
            except ValueError:
                errors["base"] = "link_conflict"
            else:
                self.options.clear()
                self.options.update(new_options)
                return self.async_create_entry(title="", data=self.options)

        return self.async_show_form(
            step_id="choose_ha_device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HA_DEVICE_ID,
                        default=suggested_ha_device_id,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=ha_device_options, mode="dropdown"
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_unlink_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and remove existing link."""
        link_rows = list_link_rows(self.config_entry)
        if not link_rows:
            return self.async_abort(reason="no_links")

        coordinator = self.config_entry.runtime_data
        device_registry = dr.async_get(self.hass)
        hb_item_names = await _async_get_hb_item_names(
            coordinator.api, [row[CONF_HB_ITEM_ID] for row in link_rows]
        )
        options = [
            selector.SelectOptionDict(
                value=f"{row[CONF_HA_DEVICE_ID]}|{row[CONF_HB_ITEM_ID]}",
                label=(
                    f"{_ha_device_label(device_registry, row[CONF_HA_DEVICE_ID])}"
                    f" -> {_hb_item_label(row[CONF_HB_ITEM_ID], hb_item_names)}"
                ),
            )
            for row in link_rows
        ]

        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input[CONF_LINK_ACTION]
            selected_ha_device_id, selected_hb_item_id = selected.split("|", 1)
            try:
                new_options = await remove_link(
                    self.config_entry,
                    coordinator.api,
                    selected_ha_device_id,
                    selected_hb_item_id,
                )
            except ValueError:
                errors["base"] = "unlink_conflict"
            else:
                self.options.clear()
                self.options.update(new_options)
                return self.async_create_entry(title="", data=self.options)

        return self.async_show_form(
            step_id="unlink_ha_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINK_ACTION): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode="dropdown")
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_resync(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually resync tagged hb_item scan."""
        coordinator = self.config_entry.runtime_data
        await async_cleanup_unlinked_hb_backlinks(
            self.hass, self.config_entry, coordinator.api
        )
        await coordinator.async_refresh()
        return self.async_create_entry(title="", data=self.options)


def _ha_device_label(device_registry: dr.DeviceRegistry, ha_device_id: str) -> str:
    """Return human-friendly label for a HA device."""
    if ha_device := device_registry.async_get(ha_device_id):
        return ha_device.name_by_user or ha_device.name or ha_device_id
    return ha_device_id


def _hb_item_label(hb_item_id: str, hb_item_names: dict[str, str]) -> str:
    """Return readable label for a HomeBox item ID."""
    if hb_item_name := hb_item_names.get(hb_item_id):
        return hb_item_name
    short_id = hb_item_id[:8]
    return f"HomeBox item ({short_id})"


async def _async_get_hb_item_names(
    api: HomeBoxApiClient, hb_item_ids: list[str]
) -> dict[str, str]:
    """Fetch HomeBox item names for display labels."""
    names: dict[str, str] = {}
    for hb_item_id in hb_item_ids:
        try:
            hb_item = await api.async_get_hb_item(hb_item_id)
        except (HomeBoxApiError, HomeBoxAuthenticationError, HomeBoxConnectionError):
            continue
        hb_item_name = hb_item.get("name")
        if isinstance(hb_item_name, str) and hb_item_name:
            names[hb_item_id] = hb_item_name
    return names


def _name_similarity(left: str | None, right: str | None) -> float:
    """Return fuzzy similarity between two names."""
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 0.0

    ratio = SequenceMatcher(a=left_norm, b=right_norm).ratio()
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return ratio

    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(ratio, overlap)


def _normalize_name(value: str | None) -> str:
    """Normalize name for fuzzy matching."""
    if not value:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(normalized.split())
