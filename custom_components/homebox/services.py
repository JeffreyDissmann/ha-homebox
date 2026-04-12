"""HomeBox HA service actions."""

from __future__ import annotations

import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .api import HomeBoxApiClient
from .const import (
    ATTR_MAINTENANCE_DESCRIPTION,
    ATTR_MAINTENANCE_NAME,
    ATTR_MAINTENANCE_SCHEDULED_DATE,
    DOMAIN,
    SERVICE_ADD_MAINTENANCE,
    SERVICE_CLEAR_MAINTENANCE,
    SERVICE_DELETE_MAINTENANCE,
)
from .linking import get_link_maps

ADD_MAINTENANCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MAINTENANCE_NAME): cv.string,
        vol.Optional(ATTR_MAINTENANCE_DESCRIPTION, default=""): cv.string,
        vol.Optional(ATTR_MAINTENANCE_SCHEDULED_DATE): cv.date,
    }
)

DELETE_MAINTENANCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MAINTENANCE_NAME): cv.string,
    }
)

CLEAR_MAINTENANCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)


def _is_entry_completed(entry: dict[str, Any]) -> bool:
    completed_date = entry.get("completedDate")
    return isinstance(completed_date, str) and bool(completed_date)


def _resolve_linked_item(
    hass: HomeAssistant, entity_id: str
) -> tuple[HomeBoxApiClient, str]:
    """Resolve entity_id to (api_client, hb_item_id) via the linking maps.

    Raises ServiceValidationError if the entity or its device is not linked.
    """
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    if entity_entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_not_found",
            translation_placeholders={"entity_id": entity_id},
        )

    device_id = entity_entry.device_id
    if device_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_has_no_device",
            translation_placeholders={"entity_id": entity_id},
        )

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state != ConfigEntryState.LOADED:
            continue
        ha_device_to_hb_item, _ = get_link_maps(entry)
        hb_item_id = ha_device_to_hb_item.get(device_id)
        if hb_item_id:
            coordinator = entry.runtime_data
            return coordinator.api, hb_item_id

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_linked",
        translation_placeholders={"entity_id": entity_id},
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register HomeBox service actions (idempotent — safe to call per entry)."""

    if hass.services.has_service(DOMAIN, SERVICE_ADD_MAINTENANCE):
        return

    async def _handle_add_maintenance(call: ServiceCall) -> None:
        api, hb_item_id = _resolve_linked_item(hass, call.data[ATTR_ENTITY_ID])
        scheduled: datetime.date = call.data.get(
            ATTR_MAINTENANCE_SCHEDULED_DATE, dt_util.now().date()
        )
        await api.async_create_hb_item_maintenance(
            hb_item_id,
            name=call.data[ATTR_MAINTENANCE_NAME],
            description=call.data.get(ATTR_MAINTENANCE_DESCRIPTION, ""),
            scheduled_date=scheduled.isoformat(),
        )

    async def _handle_delete_maintenance(call: ServiceCall) -> None:
        api, hb_item_id = _resolve_linked_item(hass, call.data[ATTR_ENTITY_ID])
        target_name = call.data[ATTR_MAINTENANCE_NAME]
        entries = await api.async_get_hb_item_maintenance(hb_item_id, status="both")
        deleted = 0
        for entry in entries:
            if entry.get("name") == target_name and not _is_entry_completed(entry):
                await api.async_delete_hb_maintenance(entry["id"])
                deleted += 1
        if deleted == 0:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="maintenance_not_found",
                translation_placeholders={"name": target_name},
            )

    async def _handle_clear_maintenance(call: ServiceCall) -> None:
        api, hb_item_id = _resolve_linked_item(hass, call.data[ATTR_ENTITY_ID])
        entries = await api.async_get_hb_item_maintenance(hb_item_id, status="both")
        for entry in entries:
            if not _is_entry_completed(entry):
                await api.async_delete_hb_maintenance(entry["id"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_MAINTENANCE,
        _handle_add_maintenance,
        ADD_MAINTENANCE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_MAINTENANCE,
        _handle_delete_maintenance,
        DELETE_MAINTENANCE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_MAINTENANCE,
        _handle_clear_maintenance,
        CLEAR_MAINTENANCE_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister HomeBox services when no config entries remain."""
    if hass.config_entries.async_entries(DOMAIN):
        return
    hass.services.async_remove(DOMAIN, SERVICE_ADD_MAINTENANCE)
    hass.services.async_remove(DOMAIN, SERVICE_DELETE_MAINTENANCE)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_MAINTENANCE)
