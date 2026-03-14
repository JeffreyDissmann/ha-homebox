"""Helpers for HomeBox item custom fields and full-update payloads."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from .const import LINK_BACKLINK_FIELD_NAME


def extract_item_fields(hb_item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract valid custom fields from a HomeBox item."""
    fields = hb_item.get("fields")
    if not isinstance(fields, list):
        return []
    return [field for field in fields if isinstance(field, dict)]


def is_backlink_field(field: Mapping[str, Any]) -> bool:
    """Return True if a field is the managed Home Assistant backlink field."""
    name = field.get("name")
    if isinstance(name, str) and name == LINK_BACKLINK_FIELD_NAME:
        return True

    if name not in (None, ""):
        return False

    text_value = field.get("textValue")
    if not isinstance(text_value, str):
        return False

    return bool(re.search(r"/config/devices/device/[^/?#\s]+/?$", text_value.strip()))


def merge_backlink_field(
    fields: list[dict[str, Any]], backlink_url: str | None
) -> list[dict[str, Any]]:
    """Merge Home Assistant backlink custom field into fields list."""
    merged: list[dict[str, Any]] = []
    found = False

    for field in fields:
        if is_backlink_field(field):
            found = True
            merged.append(
                {
                    "id": field.get("id"),
                    "type": "text",
                    "name": field.get("name") or LINK_BACKLINK_FIELD_NAME,
                    "textValue": backlink_url or "",
                    "numberValue": field.get("numberValue") or 0,
                    "booleanValue": field.get("booleanValue") or False,
                }
            )
            continue
        merged.append(field)

    if not found and backlink_url:
        merged.append(
            {
                "type": "text",
                "name": LINK_BACKLINK_FIELD_NAME,
                "textValue": backlink_url,
                "numberValue": 0,
                "booleanValue": False,
            }
        )

    return merged


def build_item_update_payload(
    hb_item: Mapping[str, Any], fields: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build full PUT payload preserving HomeBox item values."""
    payload: dict[str, Any] = {
        "id": hb_item.get("id"),
        "name": hb_item.get("name", ""),
        "description": hb_item.get("description", "") or "",
        "insured": bool(hb_item.get("insured", False)),
        "archived": bool(hb_item.get("archived", False)),
        "quantity": int(hb_item.get("quantity", 1) or 1),
        "assetId": hb_item.get("assetId", "") or "",
        "manufacturer": hb_item.get("manufacturer", "") or "",
        "modelNumber": hb_item.get("modelNumber", "") or "",
        "notes": hb_item.get("notes", "") or "",
        "purchaseFrom": hb_item.get("purchaseFrom", "") or "",
        "purchasePrice": hb_item.get("purchasePrice"),
        "purchaseTime": hb_item.get("purchaseTime"),
        "serialNumber": hb_item.get("serialNumber", "") or "",
        "soldNotes": hb_item.get("soldNotes", "") or "",
        "soldPrice": hb_item.get("soldPrice"),
        "soldTime": hb_item.get("soldTime"),
        "soldTo": hb_item.get("soldTo", "") or "",
        "warrantyDetails": hb_item.get("warrantyDetails", "") or "",
        "warrantyExpires": hb_item.get("warrantyExpires"),
        "lifetimeWarranty": bool(hb_item.get("lifetimeWarranty", False)),
        "syncChildItemsLocations": bool(hb_item.get("syncChildItemsLocations", False)),
        "fields": fields,
        "tagIds": [
            tag["id"]
            for tag in hb_item.get("tags", [])
            if isinstance(tag, dict) and isinstance(tag.get("id"), str)
        ],
    }

    location = hb_item.get("location")
    if isinstance(location, dict) and isinstance(location.get("id"), str):
        payload["locationId"] = location["id"]

    parent = hb_item.get("parent")
    if isinstance(parent, dict) and isinstance(parent.get("id"), str):
        payload["parentId"] = parent["id"]

    return payload
