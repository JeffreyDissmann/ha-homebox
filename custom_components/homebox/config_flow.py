"""Config flow for the HomeBox integration."""

from __future__ import annotations

from difflib import SequenceMatcher
import logging
import re
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.components import persistent_notification
from homeassistant.config_entries import (
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    selector,
    translation as ha_translation,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HomeBoxApiClient,
    HomeBoxApiError,
    HomeBoxAuthenticationError,
    HomeBoxConnectionError,
    HomeBoxImageContentTypeError,
    HomeBoxImageDownloadError,
    HomeBoxImageTooLargeError,
    HomeBoxInvalidImageUrlError,
    normalize_homebox_host,
)
from .const import (
    CONF_AREA,
    CONF_HA_DEVICE_ID,
    CONF_HB_ITEM_DESCRIPTION,
    CONF_HB_ITEM_IMAGE_URL,
    CONF_HB_ITEM_MANUFACTURER,
    CONF_HB_ITEM_MODEL_NUMBER,
    CONF_HB_ITEM_NAME,
    CONF_HB_ITEM_PURCHASE_PRICE,
    CONF_HB_ITEM_SERIAL_NUMBER,
    DEFAULT_NAME,
    DOMAIN,
)
from .linking import (
    apply_link,
    async_cleanup_unlinked_hb_backlinks,
    get_link_maps,
    remove_link,
)

_LOGGER = logging.getLogger(__name__)
_MANUAL_HA_DEVICE_SELECTION = "__manual__"
_MAX_SUGGESTED_HA_DEVICES = 3

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

    normalized_host = normalize_homebox_host(data[CONF_HOST])
    return {
        "title": data.get(CONF_NAME) or URL(normalized_host).host or normalized_host
    }


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeBox."""

    VERSION = 1
    _auth_error_detail: str = "No authentication attempt yet."
    _discovery_entry_id: str | None = None
    _discovery_hb_item_id: str | None = None
    _discovery_hb_item_name: str | None = None
    _discovery_hb_item_details: str = ""
    _discovery_hb_item_manufacturer: str | None = None
    _discovery_hb_item_model: str | None = None
    _discovery_suggested_ha_device_ids: list[str] = []
    _config_translations: dict[str, str] | None = None

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
            normalized_host = normalize_homebox_host(user_input[CONF_HOST])
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

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle integration discovery for unlinked tagged HomeBox items."""
        if self.source != SOURCE_INTEGRATION_DISCOVERY:
            return self.async_abort(reason="unknown")

        entry_id = discovery_info.get("config_entry_id")
        hb_item_id = discovery_info.get("hb_item_id")
        hb_item_name = discovery_info.get("hb_item_name")
        if not isinstance(entry_id, str) or not isinstance(hb_item_id, str):
            return self.async_abort(reason="missing_config_entry")
        if not isinstance(hb_item_name, str) or not hb_item_name:
            hb_item_name = hb_item_id

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason="missing_config_entry")

        await self.async_set_unique_id(f"{entry_id}:{hb_item_id}")
        self._abort_if_unique_id_configured()

        _, hb_item_to_ha_device = get_link_maps(entry)
        if hb_item_id in hb_item_to_ha_device:
            return self.async_abort(reason="already_linked")

        if not getattr(entry, "runtime_data", None):
            return self.async_abort(reason="missing_config_entry")

        coordinator = entry.runtime_data
        unlinked_hb_item_ids = {
            tagged_item.hb_item_id for tagged_item in coordinator.data.unlinked_hb_items
        }
        if hb_item_id not in unlinked_hb_item_ids:
            return self.async_abort(reason="hb_item_not_available")

        hb_item_data: dict[str, Any] = {}
        try:
            hb_item_data = await coordinator.api.async_get_hb_item(hb_item_id)
        except (
            HomeBoxApiError,
            HomeBoxAuthenticationError,
            HomeBoxConnectionError,
        ):
            hb_item_data = {}
        discovered_name = hb_item_data.get("name")
        if isinstance(discovered_name, str) and discovered_name:
            hb_item_name = discovered_name

        self._discovery_entry_id = entry_id
        self._discovery_hb_item_id = hb_item_id
        self._discovery_hb_item_name = hb_item_name
        self._discovery_hb_item_details = _format_hb_item_metadata(hb_item_data)
        self._discovery_hb_item_manufacturer = _safe_str(hb_item_data.get("manufacturer"))
        self._discovery_hb_item_model = _safe_str(hb_item_data.get("modelNumber"))
        self.context["title_placeholders"] = {
            "name": hb_item_name,
            "hb_item_name": hb_item_name,
        }
        self.async_update_title_placeholders({"name": hb_item_name})

        return await self.async_step_link_discovered_hb_item()

    async def async_step_link_discovered_hb_item(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Link a discovered HomeBox item to an unlinked Home Assistant device."""
        if self._discovery_entry_id is None or self._discovery_hb_item_id is None:
            return self.async_abort(reason="missing_hb_item")

        entry = self.hass.config_entries.async_get_entry(self._discovery_entry_id)
        if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            return self.async_abort(reason="missing_config_entry")

        available_devices = _get_unlinked_named_ha_devices(self.hass, entry)
        if not available_devices:
            return self.async_abort(reason="no_unlinked_ha_devices")

        ranked_devices = _rank_ha_device_candidates(
            self._discovery_hb_item_name,
            available_devices,
            self._discovery_hb_item_manufacturer,
            self._discovery_hb_item_model,
        )
        suggested_pairs = ranked_devices[: min(_MAX_SUGGESTED_HA_DEVICES, len(ranked_devices))]
        suggested_devices = [device for _, device in suggested_pairs]
        self._discovery_suggested_ha_device_ids = [device.id for device in suggested_devices]

        options = [
            selector.SelectOptionDict(
                value=device.id,
                label=_ha_device_candidate_label(device),
            )
            for device in suggested_devices
        ]
        manual_option_label = await self._async_get_config_translation(
            "step.link_discovered_hb_item.data.manual_option",
            "Select manually",
        )
        options.append(
            selector.SelectOptionDict(
                value=_MANUAL_HA_DEVICE_SELECTION,
                label=manual_option_label,
            )
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_ha_device_id = user_input[CONF_HA_DEVICE_ID]
            if selected_ha_device_id == _MANUAL_HA_DEVICE_SELECTION:
                return await self.async_step_select_discovered_manual_ha_device()
            if (
                selected_ha_device_id not in self._discovery_suggested_ha_device_ids
                or not await self._async_apply_discovery_link(entry, selected_ha_device_id)
            ):
                errors["base"] = "link_conflict"
            else:
                return self.async_abort(reason="link_created")

        return self.async_show_form(
            step_id="link_discovered_hb_item",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HA_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, mode="dropdown")
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "hb_item_name": self._discovery_hb_item_name or self._discovery_hb_item_id,
                "hb_item_details": self._discovery_hb_item_details
                or await self._async_get_config_translation(
                    "step.link_discovered_hb_item.data.no_item_details",
                    "No additional HomeBox item details available.",
                ),
                "suggested_count": str(len(suggested_devices)),
            },
        )

    async def async_step_select_discovered_manual_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually select any unlinked Home Assistant device for discovered item."""
        if self._discovery_entry_id is None or self._discovery_hb_item_id is None:
            return self.async_abort(reason="missing_hb_item")

        entry = self.hass.config_entries.async_get_entry(self._discovery_entry_id)
        if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            return self.async_abort(reason="missing_config_entry")

        available_device_ids = {
            device.id for device in _get_unlinked_named_ha_devices(self.hass, entry)
        }
        if not available_device_ids:
            return self.async_abort(reason="no_unlinked_ha_devices")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_ha_device_id = user_input[CONF_HA_DEVICE_ID]
            if (
                selected_ha_device_id not in available_device_ids
                or not await self._async_apply_discovery_link(entry, selected_ha_device_id)
            ):
                errors["base"] = "link_conflict"
            else:
                return self.async_abort(reason="link_created")

        return self.async_show_form(
            step_id="select_discovered_manual_ha_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HA_DEVICE_ID): selector.DeviceSelector(
                        selector.DeviceSelectorConfig()
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "hb_item_name": self._discovery_hb_item_name or self._discovery_hb_item_id,
            },
        )

    async def _async_apply_discovery_link(
        self, entry: ConfigEntry, ha_device_id: str
    ) -> bool:
        """Apply discovered link and refresh coordinator state."""
        coordinator = entry.runtime_data
        try:
            new_options = await apply_link(
                self.hass,
                entry,
                coordinator.api,
                ha_device_id,
                self._discovery_hb_item_id,
            )
        except ValueError:
            return False

        self.hass.config_entries.async_update_entry(entry, options=new_options)
        await coordinator.async_refresh()
        return True

    async def _async_get_config_translation(self, key: str, default: str) -> str:
        """Return a localized config translation string by relative key."""
        if self._config_translations is None:
            self._config_translations = await ha_translation.async_get_translations(
                self.hass,
                self.hass.config.language,
                "config",
                integrations=[DOMAIN],
            )
        full_key = f"component.{DOMAIN}.config.{key}"
        return self._config_translations.get(full_key, default)


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
        self._selected_ha_device_id_for_create: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options for HomeBox."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "create_hb_item_from_ha_device",
                "unlink_ha_device",
                "resync",
            ],
        )

    async def async_step_create_hb_item_from_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an unlinked HA device to create a HomeBox item from."""
        if not _get_unlinked_named_ha_devices(self.hass, self.config_entry):
            return self.async_abort(reason="no_unlinked_ha_devices")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_device_id = user_input[CONF_HA_DEVICE_ID]
            available_ids = {
                device.id
                for device in _get_unlinked_named_ha_devices(
                    self.hass, self.config_entry
                )
            }
            if selected_device_id not in available_ids:
                errors["base"] = "link_conflict"
            else:
                self._selected_ha_device_id_for_create = selected_device_id
                return await self.async_step_create_hb_item_details()

        return self.async_show_form(
            step_id="create_hb_item_from_ha_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HA_DEVICE_ID): selector.DeviceSelector(
                        selector.DeviceSelectorConfig()
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_create_hb_item_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review/edit HomeBox item details and create + link item."""
        selected_ha_device_id = self._selected_ha_device_id_for_create
        if selected_ha_device_id is None:
            return self.async_abort(reason="missing_ha_device")

        device_registry = dr.async_get(self.hass)
        ha_device = device_registry.async_get(selected_ha_device_id)
        if ha_device is None:
            return self.async_abort(reason="missing_ha_device")

        device_name = ha_device.name_by_user or ha_device.name or ha_device.id
        manufacturer = ha_device.manufacturer or ""
        model_number = _format_ha_model_number(ha_device)
        serial_number = ha_device.serial_number or ""
        area_name = _get_ha_device_area_name(self.hass, ha_device)
        errors: dict[str, str] = {}

        if user_input is not None:
            purchase_price = float(user_input[CONF_HB_ITEM_PURCHASE_PRICE])
            if purchase_price < 0:
                errors["base"] = "create_hb_item_failed"
            else:
                coordinator = self.config_entry.runtime_data
                api = coordinator.api
                created_hb_item_id: str | None = None
                image_warning: str | None = None
                try:
                    ha_device_to_hb_item, _ = get_link_maps(self.config_entry)
                    if selected_ha_device_id in ha_device_to_hb_item:
                        errors["base"] = "link_conflict"
                    else:
                        tag_id = await api.async_ensure_link_tag()
                        location_id = (
                            await api.async_ensure_location_by_name(area_name)
                            if area_name
                            else None
                        )
                        created_hb_item_id = await api.async_create_hb_item(
                            name=user_input[CONF_HB_ITEM_NAME],
                            location_id=location_id,
                            tag_ids=[tag_id],
                        )
                        await api.async_update_hb_item_details(
                            created_hb_item_id,
                            name=user_input[CONF_HB_ITEM_NAME],
                            description=user_input.get(CONF_HB_ITEM_DESCRIPTION, ""),
                            manufacturer=user_input.get(CONF_HB_ITEM_MANUFACTURER, ""),
                            model_number=user_input.get(CONF_HB_ITEM_MODEL_NUMBER, ""),
                            serial_number=user_input.get(CONF_HB_ITEM_SERIAL_NUMBER, ""),
                            purchase_price=purchase_price,
                            location_id=location_id,
                        )

                        image_url = user_input.get(CONF_HB_ITEM_IMAGE_URL, "").strip()
                        if image_url:
                            try:
                                await api.async_add_hb_item_photo_from_url(
                                    created_hb_item_id, image_url
                                )
                            except HomeBoxInvalidImageUrlError:
                                image_warning = "invalid_image_url"
                            except HomeBoxImageContentTypeError:
                                image_warning = "image_not_image"
                            except HomeBoxImageTooLargeError:
                                image_warning = "image_too_large"
                            except HomeBoxImageDownloadError:
                                image_warning = "image_download_failed"
                            except (
                                HomeBoxApiError,
                                HomeBoxAuthenticationError,
                                HomeBoxConnectionError,
                            ):
                                image_warning = "image_download_failed"

                        # Apply standard link side effects and option persistence.
                        new_options = await apply_link(
                            self.hass,
                            self.config_entry,
                            api,
                            selected_ha_device_id,
                            created_hb_item_id,
                        )
                        if image_warning is not None:
                            persistent_notification.async_create(
                                self.hass,
                                (
                                    "HomeBox item was created and linked, but image upload "
                                    f"failed ({image_warning}). You can add the image later "
                                    "directly in HomeBox."
                                ),
                                title="HomeBox image upload warning",
                                notification_id=(
                                    f"{DOMAIN}_image_upload_warning_"
                                    f"{self.config_entry.entry_id}"
                                ),
                            )
                        self.options.clear()
                        self.options.update(new_options)
                        return self.async_create_entry(title="", data=self.options)
                except ValueError:
                    errors["base"] = "link_conflict"
                except (
                    HomeBoxApiError,
                    HomeBoxAuthenticationError,
                    HomeBoxConnectionError,
                ):
                    errors["base"] = "create_hb_item_failed"
                finally:
                    if (
                        errors
                        and created_hb_item_id is not None
                        and errors.get("base") != "image_download_failed"
                    ):
                        try:
                            await api.async_delete_hb_item(created_hb_item_id)
                        except (
                            HomeBoxApiError,
                            HomeBoxAuthenticationError,
                            HomeBoxConnectionError,
                        ):
                            _LOGGER.warning(
                                "Unable to roll back HomeBox item %s after create/link failure",
                                created_hb_item_id,
                            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HB_ITEM_NAME, default=device_name): str,
                vol.Optional(
                    CONF_HB_ITEM_MANUFACTURER, default=manufacturer
                ): str,
                vol.Optional(CONF_HB_ITEM_MODEL_NUMBER, default=model_number): str,
                vol.Optional(CONF_HB_ITEM_SERIAL_NUMBER, default=serial_number): str,
                vol.Optional(
                    CONF_HB_ITEM_DESCRIPTION,
                    default=f"Created from Home Assistant device: {device_name}",
                ): str,
                vol.Required(
                    CONF_HB_ITEM_PURCHASE_PRICE,
                    default=0.0,
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(CONF_HB_ITEM_IMAGE_URL): str,
            }
        )

        return self.async_show_form(
            step_id="create_hb_item_details",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "device_name": device_name,
                "location_name": area_name or "No area assigned in Home Assistant",
            },
        )

    async def async_step_unlink_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and remove existing link."""
        ha_device_to_hb_item, _ = get_link_maps(self.config_entry)
        if not ha_device_to_hb_item:
            return self.async_abort(reason="no_links")

        device_registry = dr.async_get(self.hass)
        device_options = [
            selector.SelectOptionDict(
                value=ha_device_id,
                label=_ha_device_label(device_registry, ha_device_id),
            )
            for ha_device_id in sorted(
                ha_device_to_hb_item,
                key=lambda device_id: _ha_device_label(device_registry, device_id).lower(),
            )
        ]

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_ha_device_id = user_input[CONF_HA_DEVICE_ID]
            selected_hb_item_id = ha_device_to_hb_item.get(selected_ha_device_id)
            if selected_hb_item_id is None:
                errors["base"] = "unlink_conflict"
                return self.async_show_form(
                    step_id="unlink_ha_device",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_HA_DEVICE_ID): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=device_options, mode="dropdown"
                                )
                            )
                        }
                    ),
                    errors=errors,
                )

            coordinator = self.config_entry.runtime_data
            try:
                new_options = await remove_link(
                    self.hass,
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
                    vol.Required(CONF_HA_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=device_options, mode="dropdown")
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
        _, new_options = await async_cleanup_unlinked_hb_backlinks(
            self.hass, self.config_entry, coordinator.api
        )
        if new_options is not None:
            self.options.clear()
            self.options.update(new_options)
        await coordinator.async_refresh()
        return self.async_create_entry(title="", data=self.options)


def _ha_device_label(device_registry: dr.DeviceRegistry, ha_device_id: str) -> str:
    """Return human-friendly label for a HA device."""
    if ha_device := device_registry.async_get(ha_device_id):
        return ha_device.name_by_user or ha_device.name or ha_device_id
    return ha_device_id


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


def _rank_ha_device_candidates(
    hb_item_name: str | None,
    devices: list[dr.DeviceEntry],
    hb_manufacturer: str | None,
    hb_model: str | None,
) -> list[tuple[float, dr.DeviceEntry]]:
    """Rank Home Assistant device candidates for a HomeBox item."""
    def _score(device: dr.DeviceEntry) -> float:
        device_name = device.name_by_user or device.name or ""
        score = _name_similarity(hb_item_name, device_name) * 1.8
        if hb_manufacturer and device.manufacturer:
            score += _name_similarity(hb_manufacturer, device.manufacturer) * 0.8
        if hb_model and device.model:
            score += _name_similarity(hb_model, device.model) * 0.8
        return score

    return sorted(
        ((_score(device), device) for device in devices),
        key=lambda result: result[0],
        reverse=True,
    )


def _format_hb_item_metadata(hb_item: dict[str, Any]) -> str:
    """Build optional HomeBox item metadata lines for discovery UI."""
    fields: list[tuple[str, str | None]] = [
        ("Description", _safe_str(hb_item.get("description"))),
        ("Manufacturer", _safe_str(hb_item.get("manufacturer"))),
        ("Model", _safe_str(hb_item.get("modelNumber"))),
        ("Serial number", _safe_str(hb_item.get("serialNumber"))),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    return "\n".join(lines)


def _ha_device_candidate_label(device: dr.DeviceEntry) -> str:
    """Build a concise Home Assistant device label with context."""
    base_name = device.name_by_user or device.name or device.id
    details = [
        part
        for part in (device.manufacturer, _safe_str(device.model))
        if part
    ]
    if not details:
        return base_name
    return f"{base_name} • {' • '.join(details)}"


def _safe_str(value: Any) -> str | None:
    """Return a stripped string value or None."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _normalize_name(value: str | None) -> str:
    """Normalize name for fuzzy matching."""
    if not value:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(normalized.split())


def _get_ha_device_area_name(
    hass: HomeAssistant, ha_device: dr.DeviceEntry
) -> str | None:
    """Return area name for a Home Assistant device."""
    if not ha_device.area_id:
        return None
    area_registry = ar.async_get(hass)
    if area := area_registry.async_get_area(ha_device.area_id):
        return area.name
    return None


def _format_ha_model_number(ha_device: dr.DeviceEntry) -> str:
    """Build a model number string from HA device model + model_id."""
    model = (ha_device.model or "").strip()
    model_id = (ha_device.model_id or "").strip()
    if model and model_id:
        if model_id in model:
            return model
        return f"{model} ({model_id})"
    return model or model_id


def _get_unlinked_named_ha_devices(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> list[dr.DeviceEntry]:
    """Return HA devices with names that are not linked to HomeBox items."""
    device_registry = dr.async_get(hass)
    ha_device_to_hb_item, _ = get_link_maps(config_entry)
    linked_ha_device_ids = set(ha_device_to_hb_item)
    return [
        device
        for device in device_registry.devices.values()
        if (device.name_by_user or device.name)
        and device.id not in linked_ha_device_ids
        and not any(identifier[0] == DOMAIN for identifier in device.identifiers)
    ]
