"""Maintenance synchronization for HomeBox battery depletion forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .api import HomeBoxApiClient
from .battery_forecast import LinkedBatteryForecast
from .const import CONF_BATTERY_MAINTENANCE, CONF_LINKS


@dataclass(slots=True)
class _BatteryNotesDetails:
    """Battery Notes details for one device."""

    battery_type: str | None
    quantity: int | None
    last_replaced: str | None


def _extract_battery_notes_details(
    hass: HomeAssistant,
    ha_device_id: str,
) -> _BatteryNotesDetails | None:
    """Extract Battery Notes attributes for a Home Assistant device."""
    if "battery_notes" not in hass.config.components:
        return None

    entity_registry = er.async_get(hass)
    best_details: _BatteryNotesDetails | None = None
    best_score = -1

    for entity_entry in er.async_entries_for_device(
        entity_registry,
        ha_device_id,
        include_disabled_entities=False,
    ):
        if entity_entry.domain != "sensor":
            continue

        state = hass.states.get(entity_entry.entity_id)
        if state is None:
            continue

        attrs = state.attributes
        battery_type = attrs.get("battery_type")
        quantity = attrs.get("battery_quantity")
        last_replaced = attrs.get("battery_last_replaced")

        if (
            battery_type is None
            and quantity is None
            and last_replaced is None
        ):
            continue

        normalized_quantity: int | None = None
        if isinstance(quantity, (int, float)):
            normalized_quantity = int(quantity)

        details = _BatteryNotesDetails(
            battery_type=str(battery_type) if isinstance(battery_type, str) else None,
            quantity=normalized_quantity,
            last_replaced=str(last_replaced) if last_replaced is not None else None,
        )
        score = 0
        if details.battery_type:
            score += 3
        if details.quantity is not None:
            score += 2
        if details.last_replaced:
            score += 2
        if "battery_plus" in entity_entry.entity_id:
            score += 2
        if score > best_score:
            best_score = score
            best_details = details

    return best_details


def _format_date_for_language(raw_date: str | None, language: str) -> str | None:
    """Format date string according to selected Home Assistant language."""
    if not raw_date:
        return None

    parsed = dt_util.parse_datetime(raw_date)
    if parsed is not None:
        value = parsed.date()
    else:
        value = dt_util.parse_date(raw_date)
        if value is None:
            return raw_date

    if language.startswith("de"):
        return value.strftime("%d.%m.%Y")
    if language.startswith("en"):
        return value.strftime("%Y-%m-%d")
    return value.isoformat()


def _build_battery_notes_lines(
    language: str, details: _BatteryNotesDetails | None
) -> list[str]:
    """Build localized lines from Battery Notes details."""
    if details is None:
        return []

    lines: list[str] = []
    formatted_last_replaced = _format_date_for_language(details.last_replaced, language)
    if language.startswith("de"):
        if details.battery_type:
            lines.append(f"Batterietyp: {details.battery_type}")
        if details.quantity is not None:
            lines.append(f"Anzahl Batterien: {details.quantity}")
        if formatted_last_replaced:
            lines.append(f"Zuletzt gewechselt: {formatted_last_replaced}")
    else:
        if details.battery_type:
            lines.append(f"Battery type: {details.battery_type}")
        if details.quantity is not None:
            lines.append(f"Battery quantity: {details.quantity}")
        if formatted_last_replaced:
            lines.append(f"Last replaced: {formatted_last_replaced}")
    return lines


def _build_maintenance_name(language: str, ha_device_name: str) -> str:
    """Build localized HomeBox maintenance name."""
    if language.startswith("de"):
        return f"Batterie wechseln: {ha_device_name}"
    return f"Replace battery: {ha_device_name}"


def _build_maintenance_description(
    language: str,
    battery_notes_lines: list[str],
) -> str:
    """Build localized HomeBox maintenance description."""
    return "\n".join(battery_notes_lines)


def _get_tracked_map(config_entry: ConfigEntry) -> dict[str, dict[str, str]]:
    """Return normalized tracked map for HomeBox maintenance entries."""
    raw = config_entry.options.get(CONF_BATTERY_MAINTENANCE, {})
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for ha_device_id, value in raw.items():
        if not isinstance(ha_device_id, str) or not isinstance(value, dict):
            continue
        hb_item_id = value.get("hb_item_id")
        hb_maintenance_id = value.get("hb_maintenance_id")
        if isinstance(hb_item_id, str) and isinstance(hb_maintenance_id, str):
            normalized[ha_device_id] = {
                "hb_item_id": hb_item_id,
                "hb_maintenance_id": hb_maintenance_id,
            }
    return normalized


def _build_options_with_tracked_map(
    config_entry: ConfigEntry, tracked: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Build updated config entry options with maintenance tracking map."""
    options = dict(config_entry.options)
    options[CONF_BATTERY_MAINTENANCE] = tracked
    options.setdefault(CONF_LINKS, config_entry.options.get(CONF_LINKS, {}))
    return options


def _find_entry_by_id(entries: list[dict[str, Any]], entry_id: str) -> dict[str, Any] | None:
    """Find a maintenance entry by ID."""
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    return None


def _is_entry_completed(entry: dict[str, Any]) -> bool:
    """Return True if a HomeBox maintenance entry is completed."""
    completed_date = entry.get("completedDate")
    return isinstance(completed_date, str) and bool(completed_date)


def _is_entry_up_to_date(
    entry: dict[str, Any],
    *,
    name: str,
    description: str,
    scheduled_date: str,
    cost: str,
) -> bool:
    """Return True if maintenance entry already matches desired values."""
    return (
        entry.get("name") == name
        and entry.get("description") == description
        and entry.get("scheduledDate") == scheduled_date
        and entry.get("cost") == cost
    )


async def async_sync_battery_maintenance_items(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: HomeBoxApiClient,
    forecasts: dict[str, LinkedBatteryForecast],
) -> dict[str, Any] | None:
    """Sync HomeBox maintenance entries for battery depletion forecasts."""
    language = getattr(hass.config, "language", "en") or "en"
    device_registry = dr.async_get(hass)
    tracked_map = _get_tracked_map(config_entry)
    updated_map = dict(tracked_map)
    changed = False

    valid_device_ids = {
        ha_device_id
        for ha_device_id, forecast in forecasts.items()
        if forecast.battery_entity_id is not None
    }
    for tracked_device_id in list(updated_map):
        if tracked_device_id not in valid_device_ids:
            updated_map.pop(tracked_device_id, None)
            changed = True

    maintenance_cache: dict[str, list[dict[str, Any]]] = {}

    async def _async_get_item_maintenance(hb_item_id: str) -> list[dict[str, Any]]:
        if hb_item_id not in maintenance_cache:
            maintenance_cache[hb_item_id] = await api.async_get_hb_item_maintenance(
                hb_item_id, status="both"
            )
        return maintenance_cache[hb_item_id]

    for ha_device_id, forecast in forecasts.items():
        if forecast.battery_entity_id is None or forecast.estimated_depletion_at is None:
            continue

        ha_device = device_registry.async_get(ha_device_id)
        if ha_device is None:
            continue

        device_name = ha_device.name_by_user or ha_device.name
        details = _extract_battery_notes_details(hass, ha_device_id)
        battery_notes_lines = _build_battery_notes_lines(
            language,
            details,
        )
        name = _build_maintenance_name(language, device_name)
        description = _build_maintenance_description(
            language,
            battery_notes_lines,
        )
        scheduled_date: date = forecast.estimated_depletion_at.date()
        scheduled_date_str = scheduled_date.isoformat()
        cost_int = details.quantity if details else None
        if cost_int is None or cost_int < 1:
            cost_int = 1
        cost_str = str(cost_int)

        tracked = updated_map.get(ha_device_id)
        tracked_hb_item_id = tracked.get("hb_item_id") if tracked else None
        tracked_maintenance_id = tracked.get("hb_maintenance_id") if tracked else None
        if tracked_hb_item_id != forecast.hb_item_id:
            tracked_maintenance_id = None

        item_entries = await _async_get_item_maintenance(forecast.hb_item_id)
        tracked_entry = (
            _find_entry_by_id(item_entries, tracked_maintenance_id)
            if tracked_maintenance_id
            else None
        )

        if tracked_entry is not None and not _is_entry_completed(tracked_entry):
            if not _is_entry_up_to_date(
                tracked_entry,
                name=name,
                description=description,
                scheduled_date=scheduled_date_str,
                cost=cost_str,
            ):
                await api.async_update_hb_maintenance(
                    tracked_maintenance_id,
                    name=name,
                    description=description,
                    scheduled_date=scheduled_date_str,
                    cost=cost_str,
                )
            if updated_map.get(ha_device_id) != {
                "hb_item_id": forecast.hb_item_id,
                "hb_maintenance_id": tracked_maintenance_id,
            }:
                updated_map[ha_device_id] = {
                    "hb_item_id": forecast.hb_item_id,
                    "hb_maintenance_id": tracked_maintenance_id,
                }
                changed = True
            continue

        new_maintenance_id = await api.async_create_hb_item_maintenance(
            forecast.hb_item_id,
            name=name,
            description=description,
            scheduled_date=scheduled_date_str,
            cost=cost_str,
        )
        if updated_map.get(ha_device_id) != {
            "hb_item_id": forecast.hb_item_id,
            "hb_maintenance_id": new_maintenance_id,
        }:
            updated_map[ha_device_id] = {
                "hb_item_id": forecast.hb_item_id,
                "hb_maintenance_id": new_maintenance_id,
            }
            changed = True

    if not changed:
        return None
    return _build_options_with_tracked_map(config_entry, updated_map)
