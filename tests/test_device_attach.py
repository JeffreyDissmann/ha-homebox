"""Linked sensors must attach to the original HA device, not create a new one."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.homebox import sensor as hb_sensor
from custom_components.homebox.const import DOMAIN


async def _setup(hass: HomeAssistant):
    source_entry = MockConfigEntry(domain="mqtt")
    source_entry.add_to_hass(hass)
    hb_entry = MockConfigEntry(domain=DOMAIN, title="HomeBox")
    hb_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    source = dev_reg.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("mqtt", "leak-1")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        name="Leak Bathroom",
    )
    return hb_entry, source


async def _add(hass, hb_entry, entities):
    platform = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
    platform.config_entry = hb_entry
    await platform.async_add_entities(entities)


async def test_sensor_joins_original_device(hass: HomeAssistant) -> None:
    hb_entry, source = await _setup(hass)
    sensor = hb_sensor.HomeBoxLinkedItemIdSensor(
        MagicMock(), hb_entry.entry_id, source.id, "item-1", source
    )
    await _add(hass, hb_entry, [sensor])

    ent = er.async_get(hass).async_get(sensor.entity_id)
    assert ent.device_id == source.id
    dev_reg = dr.async_get(hass)
    assert dr.async_entries_for_config_entry(dev_reg, hb_entry.entry_id) == []


async def test_existing_fork_is_removed_and_entity_relinked(hass: HomeAssistant) -> None:
    hb_entry, source = await _setup(hass)
    dev_reg = dr.async_get(hass)
    fork = dev_reg.async_get_or_create(
        config_entry_id=hb_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        name="HomeBox",
    )
    assert fork.id != source.id
    ent_reg = er.async_get(hass)
    ent = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{hb_entry.entry_id}_{source.id}_hb_item_id",
        config_entry=hb_entry, device_id=fork.id,
    )

    hb_sensor.helper_integration.async_remove_helper_devices(
        hass, helper_config_entry_id=hb_entry.entry_id, source_device_id=source.id
    )

    assert dev_reg.async_get(fork.id) is None
    assert ent_reg.async_get(ent.entity_id).device_id == source.id
