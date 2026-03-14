"""Linking helpers between HA devices and HomeBox items."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
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


def get_link_maps(config_entry: ConfigEntry) -> tuple[dict[str, str], dict[str, str]]:
    """Return normalized HA-device <-> HB-item link maps from entry options."""
    links = config_entry.options.get(CONF_LINKS, {})
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
) -> dict[str, Any]:
    """Build options payload with updated link maps."""
    options = dict(config_entry.options)
    options[CONF_LINKS] = {
        CONF_HA_DEVICE_TO_HB_ITEM: ha_device_to_hb_item,
        CONF_HB_ITEM_TO_HA_DEVICE: hb_item_to_ha_device,
    }
    return options


def get_ha_device_url(hass, ha_device_id: str) -> str:
    """Build Home Assistant URL for a device page."""
    try:
        base_url = get_url(hass)
    except NoURLAvailableError:
        return f"/config/devices/device/{ha_device_id}"
    return f"{base_url.rstrip('/')}/config/devices/device/{ha_device_id}"


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
        hb_item_id = tagged_item.get("id")
        hb_item_name = tagged_item.get("name", "Unknown")
        if not isinstance(hb_item_id, str):
            continue

        mapped_ha_device_id = hb_item_to_ha_device.get(hb_item_id)
        if mapped_ha_device_id:
            if ha_device_to_hb_item.get(mapped_ha_device_id) != hb_item_id:
                conflicts.append(
                    f"Inconsistent map for hb_item={hb_item_id} and ha_device={mapped_ha_device_id}"
                )
            continue

        full_hb_item = await api.async_get_hb_item(hb_item_id)
        item_fields = full_hb_item.get("fields", [])
        has_backlink = False
        if isinstance(item_fields, list):
            for field in item_fields:
                if (
                    isinstance(field, dict)
                    and field.get("name") == LINK_BACKLINK_FIELD_NAME
                    and isinstance(field.get("textValue"), str)
                    and field.get("textValue")
                ):
                    has_backlink = True
                    break

        if not has_backlink:
            unlinked_hb_items.append(
                HomeBoxTaggedItem(
                    hb_item_id=hb_item_id,
                    name=str(hb_item_name),
                    has_backlink=has_backlink,
                )
            )

    return HomeBoxLinkScanResult(unlinked_hb_items=unlinked_hb_items, conflicts=conflicts)


async def apply_link(
    hass,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    ha_device_id: str,
    hb_item_id: str,
) -> dict[str, Any]:
    """Apply 1:1 link and write backlink into HomeBox item."""
    ha_device_to_hb_item, hb_item_to_ha_device = get_link_maps(config_entry)
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

    device_registry = dr.async_get(hass)
    if ha_device := device_registry.async_get(ha_device_id):
        if not ha_device.configuration_url:
            device_registry.async_update_device(
                ha_device_id, configuration_url=api.get_hb_item_url(hb_item_id)
            )

    ha_device_to_hb_item[ha_device_id] = hb_item_id
    hb_item_to_ha_device[hb_item_id] = ha_device_id
    return build_updated_options(config_entry, ha_device_to_hb_item, hb_item_to_ha_device)


async def remove_link(
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

    ha_device_to_hb_item.pop(ha_device_id, None)
    hb_item_to_ha_device.pop(hb_item_id, None)
    return build_updated_options(config_entry, ha_device_to_hb_item, hb_item_to_ha_device)


def list_link_rows(config_entry: ConfigEntry) -> list[dict[str, str]]:
    """Return list of link rows for UI selectors."""
    ha_device_to_hb_item, _ = get_link_maps(config_entry)
    return [
        {CONF_HA_DEVICE_ID: ha_device_id, CONF_HB_ITEM_ID: hb_item_id}
        for ha_device_id, hb_item_id in ha_device_to_hb_item.items()
    ]
