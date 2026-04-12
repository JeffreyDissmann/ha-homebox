"""Linking helpers between HA devices and HomeBox items."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import HomeBoxApiClient
from .const import (
    CONF_HA_DEVICE_ID,
    CONF_HA_DEVICE_TO_HB_ITEM,
    CONF_HB_ITEM_ID,
    CONF_HB_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    LINK_BACKLINK_FIELD_NAME,
)


@dataclass(slots=True, frozen=True)
class HomeBoxTaggedItem:
    """Tagged HomeBox item relevant for HA linking."""

    hb_item_id: str
    name: str
    has_backlink: bool


@dataclass(slots=True, frozen=True)
class HomeBoxLinkScanResult:
    """Result of scanning HomeBox tagged items for link status."""

    unlinked_hb_items: list[HomeBoxTaggedItem]
    conflicts: list[str]


def get_link_maps(
    config_entry: ConfigEntry,
    options: Mapping[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return normalized HA-device <-> HB-item link maps from entry options."""
    source_options = options if options is not None else config_entry.options
    links = source_options.get(CONF_LINKS, {})
    ha_device_to_hb_item = links.get(CONF_HA_DEVICE_TO_HB_ITEM, {})
    hb_item_to_ha_device = links.get(CONF_HB_ITEM_TO_HA_DEVICE, {})

    if not isinstance(ha_device_to_hb_item, dict):
        ha_device_to_hb_item = {}
    if not isinstance(hb_item_to_ha_device, dict):
        hb_item_to_ha_device = {}

    return (
        {str(k): str(v) for k, v in ha_device_to_hb_item.items()},
        {str(k): str(v) for k, v in hb_item_to_ha_device.items()},
    )


def build_updated_options(
    config_entry: ConfigEntry,
    ha_device_to_hb_item: dict[str, str],
    hb_item_to_ha_device: dict[str, str],
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build options payload with updated link maps."""
    updated_options = dict(options if options is not None else config_entry.options)
    updated_options[CONF_LINKS] = {
        CONF_HA_DEVICE_TO_HB_ITEM: ha_device_to_hb_item,
        CONF_HB_ITEM_TO_HA_DEVICE: hb_item_to_ha_device,
    }
    return updated_options


def get_ha_device_url(hass: HomeAssistant, ha_device_id: str) -> str:
    """Build Home Assistant URL for a device page."""
    try:
        base_url = get_url(
            hass,
            prefer_external=True,
            allow_external=True,
            allow_internal=False,
        )
    except NoURLAvailableError:
        try:
            base_url = get_url(hass)
        except NoURLAvailableError:
            return f"/config/devices/device/{ha_device_id}"
    return f"{base_url.rstrip('/')}/config/devices/device/{ha_device_id}"


def _has_backlink_in_fields(fields: list[dict[str, Any]] | None) -> bool:
    """Return True if any field looks like the managed HA backlink field."""
    if not fields:
        return False

    for field in fields:
        if (
            field.get("name") == LINK_BACKLINK_FIELD_NAME
            and isinstance(field.get("textValue"), str)
        ):
            return True
    return False


def _pop_bidirectional_link(
    ha_device_to_hb_item: dict[str, str],
    hb_item_to_ha_device: dict[str, str],
    *,
    ha_device_id: str,
    hb_item_id: str,
) -> None:
    """Remove a single link from both forward and inverse maps."""
    if ha_device_to_hb_item.get(ha_device_id) == hb_item_id:
        ha_device_to_hb_item.pop(ha_device_id, None)
    if hb_item_to_ha_device.get(hb_item_id) == ha_device_id:
        hb_item_to_ha_device.pop(hb_item_id, None)


def _pop_link_by_hb_item(
    ha_device_to_hb_item: dict[str, str],
    hb_item_to_ha_device: dict[str, str],
    hb_item_id: str,
) -> tuple[str | None, bool]:
    """Remove inverse link by hb_item and return (ha_device_id, forward_matched)."""
    ha_device_id = hb_item_to_ha_device.pop(hb_item_id, None)
    if ha_device_id is None:
        return None, False

    if ha_device_to_hb_item.get(ha_device_id) == hb_item_id:
        ha_device_to_hb_item.pop(ha_device_id, None)
        return ha_device_id, True

    return ha_device_id, False


def _pop_link_by_ha_device(
    ha_device_to_hb_item: dict[str, str],
    hb_item_to_ha_device: dict[str, str],
    ha_device_id: str,
) -> str | None:
    """Remove forward link by ha_device and return removed hb_item_id."""
    hb_item_id = ha_device_to_hb_item.pop(ha_device_id, None)
    if hb_item_id is None:
        return None
    if hb_item_to_ha_device.get(hb_item_id) == ha_device_id:
        hb_item_to_ha_device.pop(hb_item_id, None)
    return hb_item_id


async def scan_tagged_items_for_links(
    api: HomeBoxApiClient,
    config_entry: ConfigEntry,
) -> HomeBoxLinkScanResult:
    """Scan HomeBox tagged items and classify unlinked/conflicting records."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(config_entry)

    tag_id = await api.async_ensure_link_tag()
    tagged_items = await api.async_get_hb_items_by_tag(tag_id)
    conflicts: list[str] = []
    unlinked_hb_items: list[HomeBoxTaggedItem] = []

    for tagged_item in tagged_items:
        hb_item_id = tagged_item.item_id
        mapped_ha_device_id = hb_item_to_ha_device.get(hb_item_id)
        if mapped_ha_device_id:
            if ha_device_to_hb_item.get(mapped_ha_device_id) != hb_item_id:
                conflicts.append(
                    f"Inconsistent map for hb_item={hb_item_id} and ha_device={mapped_ha_device_id}"
                )
            continue

        has_backlink = _has_backlink_in_fields(tagged_item.fields)
        if not has_backlink:
            unlinked_hb_items.append(
                HomeBoxTaggedItem(
                    hb_item_id=hb_item_id,
                    name=tagged_item.name,
                    has_backlink=False,
                )
            )

    return HomeBoxLinkScanResult(unlinked_hb_items=unlinked_hb_items, conflicts=conflicts)


async def apply_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    ha_device_id: str,
    hb_item_id: str,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply 1:1 link and write backlink into HomeBox item."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(
        config_entry, options
    )
    if ha_device_id in ha_device_to_hb_item:
        raise ValueError(
            f"ha_device {ha_device_id} is already linked to hb_item "
            f"{ha_device_to_hb_item[ha_device_id]}"
        )
    if hb_item_id in hb_item_to_ha_device:
        raise ValueError(
            f"hb_item {hb_item_id} is already linked to ha_device "
            f"{hb_item_to_ha_device[hb_item_id]}"
        )

    ha_device_url = get_ha_device_url(hass, ha_device_id)
    await api.async_set_hb_item_backlink(hb_item_id, ha_device_url)

    ha_device_to_hb_item[ha_device_id] = hb_item_id
    hb_item_to_ha_device[hb_item_id] = ha_device_id

    device_registry = dr.async_get(hass)
    if ha_device := device_registry.async_get(ha_device_id):
        await async_sync_linked_hb_item_location(hass, config_entry, api, ha_device_id)
        if not ha_device.configuration_url:
            device_registry.async_update_device(
                ha_device_id, configuration_url=api.get_hb_item_url(hb_item_id)
            )

    return build_updated_options(
        config_entry,
        ha_device_to_hb_item,
        hb_item_to_ha_device,
        options,
    )


async def remove_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    ha_device_id: str,
    hb_item_id: str,
) -> dict[str, Any]:
    """Remove link and clear HomeBox backlink field."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(config_entry)
    if ha_device_to_hb_item.get(ha_device_id) != hb_item_id:
        raise ValueError(
            f"ha_device {ha_device_id} is not linked to hb_item {hb_item_id}"
        )

    await api.async_clear_hb_item_backlink(hb_item_id)
    _pop_bidirectional_link(
        ha_device_to_hb_item,
        hb_item_to_ha_device,
        ha_device_id=ha_device_id,
        hb_item_id=hb_item_id,
    )
    _async_finalize_ha_device_unlink(
        hass,
        config_entry_id=config_entry.entry_id,
        ha_device_id=ha_device_id,
        ha_device_to_hb_item=ha_device_to_hb_item,
        api=api,
        hb_item_id=hb_item_id,
    )
    return build_updated_options(config_entry, ha_device_to_hb_item, hb_item_to_ha_device)


def list_link_rows(config_entry: ConfigEntry) -> list[dict[str, str]]:
    """Return list of link rows for UI selectors."""
    ha_device_to_hb_item, _ = get_link_maps(config_entry)
    return [
        {CONF_HA_DEVICE_ID: ha_device_id, CONF_HB_ITEM_ID: hb_item_id}
        for ha_device_id, hb_item_id in ha_device_to_hb_item.items()
    ]


async def async_cleanup_unlinked_hb_backlinks(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
) -> tuple[int, dict[str, Any] | None]:
    """Clear HomeBox backlink fields for tagged items not linked in HA."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(config_entry)
    device_registry = dr.async_get(hass)
    maps_changed = False

    tag_id = await api.async_ensure_link_tag()
    tagged_items = await api.async_get_hb_items_by_tag(tag_id)
    cleaned = 0

    for tagged_item in tagged_items:
        hb_item_id = tagged_item.item_id

        mapped_ha_device_id = hb_item_to_ha_device.get(hb_item_id)
        if mapped_ha_device_id is not None:
            mapped_ha_device = device_registry.async_get(mapped_ha_device_id)
            expected_hb_item_id = ha_device_to_hb_item.get(mapped_ha_device_id)
            if mapped_ha_device is not None and expected_hb_item_id == hb_item_id:
                continue

            await api.async_clear_hb_item_backlink(hb_item_id)
            cleaned += 1
            popped_ha_device_id, had_matching_forward = _pop_link_by_hb_item(
                ha_device_to_hb_item,
                hb_item_to_ha_device,
                hb_item_id,
            )
            if popped_ha_device_id is not None:
                _async_finalize_ha_device_unlink(
                    hass,
                    config_entry_id=config_entry.entry_id,
                    ha_device_id=popped_ha_device_id,
                    ha_device_to_hb_item=ha_device_to_hb_item,
                    api=api if had_matching_forward else None,
                    hb_item_id=hb_item_id if had_matching_forward else None,
                )
                maps_changed = True
            continue

        full_hb_item = await api.async_get_hb_item(hb_item_id)
        backlink_url = _extract_backlink_url(full_hb_item)
        if not backlink_url:
            continue

        linked_ha_device_id = _extract_ha_device_id_from_url(backlink_url)
        if linked_ha_device_id is None:
            await api.async_clear_hb_item_backlink(hb_item_id)
            cleaned += 1
            continue

        linked_ha_device = device_registry.async_get(linked_ha_device_id)
        expected_hb_item_id = ha_device_to_hb_item.get(linked_ha_device_id)
        if linked_ha_device is None or expected_hb_item_id != hb_item_id:
            await api.async_clear_hb_item_backlink(hb_item_id)
            cleaned += 1

    if maps_changed:
        return (
            cleaned,
            build_updated_options(config_entry, ha_device_to_hb_item, hb_item_to_ha_device),
        )
    return cleaned, None


def _extract_backlink_url(hb_item: dict[str, Any]) -> str | None:
    """Extract Home Assistant backlink URL from HomeBox custom fields."""
    item_fields = hb_item.get("fields")
    if not isinstance(item_fields, list):
        return None

    for field in item_fields:
        if (
            isinstance(field, dict)
            and field.get("name") == LINK_BACKLINK_FIELD_NAME
            and isinstance(field.get("textValue"), str)
            and field.get("textValue")
        ):
            return field["textValue"]
    return None


def _extract_ha_device_id_from_url(url: str) -> str | None:
    """Extract Home Assistant device ID from a device URL."""
    match = re.search(r"/config/devices/device/([^/?#]+)", url)
    if not match:
        return None
    return match.group(1)


def _get_ha_device_area_name(hass: HomeAssistant, ha_device: dr.DeviceEntry) -> str | None:
    """Return area name for a Home Assistant device, if set."""
    if not ha_device.area_id:
        return None
    area_registry = ar.async_get(hass)
    if area_entry := area_registry.async_get_area(ha_device.area_id):
        return area_entry.name
    return None


async def async_sync_ha_areas_to_hb_locations(
    hass: HomeAssistant,
    api: HomeBoxApiClient,
) -> None:
    """Ensure all Home Assistant areas exist as HomeBox locations."""
    area_registry = ar.async_get(hass)
    area_names = [
        normalized_name
        for area_entry in area_registry.async_list_areas()
        if (normalized_name := " ".join(area_entry.name.split()).strip())
    ]
    await api.async_ensure_locations_by_name(area_names)


async def async_sync_linked_hb_item_location(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    ha_device_id: str,
) -> None:
    """Sync linked HomeBox item location from HA device area."""
    ha_device_to_hb_item, _ = get_link_maps(config_entry)
    hb_item_id = ha_device_to_hb_item.get(ha_device_id)
    if hb_item_id is None:
        return

    device_registry = dr.async_get(hass)
    ha_device = device_registry.async_get(ha_device_id)
    if ha_device is None:
        return

    ha_area_name = _get_ha_device_area_name(hass, ha_device)
    if not ha_area_name:
        return

    hb_location_id = await api.async_ensure_location_by_name(ha_area_name)
    await api.async_set_hb_item_location(hb_item_id, hb_location_id)


async def async_sync_all_linked_hb_item_locations(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
) -> None:
    """Sync HomeBox item locations for all linked Home Assistant devices."""
    ha_device_to_hb_item, _ = get_link_maps(config_entry)
    for ha_device_id in ha_device_to_hb_item:
        await async_sync_linked_hb_item_location(hass, config_entry, api, ha_device_id)


async def async_cleanup_removed_ha_device_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    ha_device_id: str,
) -> dict[str, Any] | None:
    """Clean up mapping and HomeBox backlink when a linked HA device is removed."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(config_entry)
    hb_item_id = _pop_link_by_ha_device(ha_device_to_hb_item, hb_item_to_ha_device, ha_device_id)
    if hb_item_id is None:
        return None

    await api.async_clear_hb_item_backlink(hb_item_id)
    _async_finalize_ha_device_unlink(
        hass,
        config_entry_id=config_entry.entry_id,
        ha_device_id=ha_device_id,
        ha_device_to_hb_item=ha_device_to_hb_item,
        api=api,
        hb_item_id=hb_item_id,
    )
    return build_updated_options(config_entry, ha_device_to_hb_item, hb_item_to_ha_device)


def _async_finalize_ha_device_unlink(
    hass: HomeAssistant,
    config_entry_id: str,
    ha_device_id: str,
    ha_device_to_hb_item: dict[str, str],
    api: HomeBoxApiClient | None,
    hb_item_id: str | None,
) -> None:
    """Apply local HA-side cleanup for a device unlink."""
    if ha_device_id in ha_device_to_hb_item:
        return

    if api is not None and hb_item_id is not None:
        _async_clear_configuration_url_if_matching(hass, ha_device_id, api, hb_item_id)

    _async_remove_linked_hb_id_entity(hass, config_entry_id, ha_device_id)
    _async_detach_ha_device_from_homebox_entry(hass, config_entry_id, ha_device_id)


def _async_clear_configuration_url_if_matching(
    hass: HomeAssistant,
    ha_device_id: str,
    api: HomeBoxApiClient,
    hb_item_id: str,
) -> None:
    """Clear device configuration URL if it points to the linked HomeBox item."""
    device_registry = dr.async_get(hass)
    if ha_device := device_registry.async_get(ha_device_id):
        hb_item_url = api.get_hb_item_url(hb_item_id).rstrip("/")
        device_url = (ha_device.configuration_url or "").rstrip("/")
        if device_url == hb_item_url:
            device_registry.async_update_device(ha_device_id, configuration_url=None)


def _async_remove_linked_hb_id_entity(
    hass: HomeAssistant, config_entry_id: str, ha_device_id: str
) -> None:
    """Remove linked HomeBox ID sensor entity from entity registry."""
    unique_id = f"{config_entry_id}_{ha_device_id}_hb_item_id"
    entity_registry = er.async_get(hass)
    if entity_id := entity_registry.async_get_entity_id(
        SENSOR_DOMAIN, "homebox", unique_id
    ):
        entity_registry.async_remove(entity_id)


def _async_detach_ha_device_from_homebox_entry(
    hass: HomeAssistant, config_entry_id: str, ha_device_id: str
) -> None:
    """Detach HomeBox config entry from linked HA device."""
    device_registry = dr.async_get(hass)
    if device := device_registry.async_get(ha_device_id):
        if config_entry_id in device.config_entries:
            device_registry.async_update_device(
                ha_device_id, remove_config_entry_id=config_entry_id
            )
