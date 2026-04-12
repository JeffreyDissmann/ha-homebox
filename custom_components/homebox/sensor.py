"""Sensor platform for HomeBox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AREA, DEFAULT_NAME, DOMAIN
from .coordinator import HomeBoxConfigEntry, HomeBoxDataUpdateCoordinator
from .linking import get_link_maps

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HomeBoxSensorEntityDescription(SensorEntityDescription):
    """Description for a HomeBox sensor entity."""

    value_key: str


SENSOR_DESCRIPTIONS: Final[tuple[HomeBoxSensorEntityDescription, ...]] = (
    HomeBoxSensorEntityDescription(
        key="total_items",
        value_key="total_items",
        translation_key="total_items",
        icon="mdi:archive",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HomeBoxSensorEntityDescription(
        key="total_locations",
        value_key="total_locations",
        translation_key="total_locations",
        icon="mdi:map-marker-multiple",
        native_unit_of_measurement="locations",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HomeBoxSensorEntityDescription(
        key="total_value",
        value_key="total_value",
        translation_key="total_value",
        icon="mdi:cash-multiple",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HomeBoxSensorEntityDescription(
        key="maintenance_due_today",
        value_key="maintenance_due_today",
        translation_key="maintenance_due_today",
        icon="mdi:calendar-today",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HomeBoxSensorEntityDescription(
        key="maintenance_due_next_week",
        value_key="maintenance_due_next_week",
        translation_key="maintenance_due_next_week",
        icon="mdi:calendar-alert",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up HomeBox sensor entities from config entry."""
    entities: list[SensorEntity] = []
    forecast_entities_added = 0
    entity_registry = er.async_get(hass)

    suggested_area_name: str | None = None
    if area_id := entry.data.get(CONF_AREA):
        area_registry = ar.async_get(hass)
        if area_entry := area_registry.async_get_area(area_id):
            suggested_area_name = area_entry.name

    entities.extend(
        HomeBoxStatisticsSensor(
            entry.runtime_data,
            entry.entry_id,
            entry.data[CONF_HOST],
            entry.data.get(CONF_NAME, DEFAULT_NAME),
            suggested_area_name,
            description,
        )
        for description in SENSOR_DESCRIPTIONS
    )

    device_registry = dr.async_get(hass)
    ha_device_to_hb_item, _ = get_link_maps(entry)
    for ha_device_id, hb_item_id in ha_device_to_hb_item.items():
        if linked_ha_device := device_registry.async_get(ha_device_id):
            entities.append(
                HomeBoxLinkedItemIdSensor(
                    entry.runtime_data,
                    entry.entry_id,
                    ha_device_id,
                    hb_item_id,
                    linked_ha_device,
                )
            )
            linked_battery_forecast = entry.runtime_data.data.linked_battery_forecasts.get(
                ha_device_id
            )
            if (
                linked_battery_forecast is not None
                and linked_battery_forecast.battery_entity_id is not None
            ):
                entities.append(
                    HomeBoxLinkedBatteryDepletionDateSensor(
                        entry.runtime_data,
                        entry.entry_id,
                        ha_device_id,
                        linked_ha_device,
                    )
                )
                forecast_entities_added += 1
            else:
                stale_unique_id = (
                    f"{entry.entry_id}_{ha_device_id}_battery_depletion_date"
                )
                stale_entity_id = entity_registry.async_get_entity_id(
                    "sensor",
                    DOMAIN,
                    stale_unique_id,
                )
                if stale_entity_id is not None:
                    entity_registry.async_remove(stale_entity_id)

    _LOGGER.debug(
        "HomeBox sensor setup: added %s entities total, including %s battery depletion sensors",
        len(entities),
        forecast_entities_added,
    )
    async_add_entities(entities)


class HomeBoxStatisticsSensor(
    CoordinatorEntity[HomeBoxDataUpdateCoordinator], SensorEntity
):
    """HomeBox statistics sensor."""

    entity_description: HomeBoxSensorEntityDescription

    def __init__(
        self,
        coordinator: HomeBoxDataUpdateCoordinator,
        config_entry_id: str,
        host: str,
        display_name: str,
        suggested_area: str | None,
        description: HomeBoxSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{config_entry_id}_{description.key}"
        self._attr_native_value = getattr(coordinator.data, description.value_key)
        self._attr_suggested_object_id = f"homebox_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry_id)},
            name=display_name or DEFAULT_NAME,
            manufacturer="HomeBox",
            configuration_url=host,
            suggested_area=suggested_area,
        )
        self._attr_has_entity_name = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = getattr(
            self.coordinator.data, self.entity_description.value_key
        )
        self.async_write_ha_state()


class HomeBoxLinkedItemIdSensor(
    CoordinatorEntity[HomeBoxDataUpdateCoordinator], SensorEntity
):
    """Diagnostic sensor exposing the linked HomeBox item ID for one HA device."""

    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "linked_homebox_id"

    def __init__(
        self,
        coordinator: HomeBoxDataUpdateCoordinator,
        config_entry_id: str,
        ha_device_id: str,
        hb_item_id: str,
        linked_ha_device: dr.DeviceEntry,
    ) -> None:
        """Initialize linked HomeBox ID sensor."""
        super().__init__(coordinator)
        self._ha_device_id = ha_device_id
        self._hb_item_id = hb_item_id
        self._attr_unique_id = f"{config_entry_id}_{ha_device_id}_hb_item_id"
        self._attr_native_value = hb_item_id

        device_info: DeviceInfo = DeviceInfo()
        if linked_ha_device.identifiers:
            device_info["identifiers"] = set(linked_ha_device.identifiers)
        if linked_ha_device.connections:
            device_info["connections"] = set(linked_ha_device.connections)
        self._attr_device_info = device_info

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return link identifiers for debugging and automations."""
        return {
            "ha_device_id": self._ha_device_id,
            "homebox_id": self._hb_item_id,
        }


class HomeBoxLinkedBatteryDepletionDateSensor(
    CoordinatorEntity[HomeBoxDataUpdateCoordinator], SensorEntity
):
    """Date sensor with estimated battery depletion for one linked HA device."""

    _attr_icon = "mdi:battery-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "estimated_battery_depletion_date"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(
        self,
        coordinator: HomeBoxDataUpdateCoordinator,
        config_entry_id: str,
        ha_device_id: str,
        linked_ha_device: dr.DeviceEntry,
    ) -> None:
        """Initialize linked battery depletion date sensor."""
        super().__init__(coordinator)
        self._ha_device_id = ha_device_id
        self._attr_unique_id = f"{config_entry_id}_{ha_device_id}_battery_depletion_date"
        self._attr_native_value = self._resolve_native_value()

        device_info: DeviceInfo = DeviceInfo()
        if linked_ha_device.identifiers:
            device_info["identifiers"] = set(linked_ha_device.identifiers)
        if linked_ha_device.connections:
            device_info["connections"] = set(linked_ha_device.connections)
        self._attr_device_info = device_info

    def _resolve_native_value(self) -> date | None:
        """Return depletion date for linked device if available."""
        if self.coordinator.data is None:
            return None
        forecast = self.coordinator.data.linked_battery_forecasts.get(self._ha_device_id)
        if forecast is None or forecast.estimated_depletion_at is None:
            return None
        return forecast.estimated_depletion_at.date()

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Return battery snapshots and trend data for this linked device."""
        if self.coordinator.data is None:
            return {"status": "no_data", "ha_device_id": self._ha_device_id}
        forecast = self.coordinator.data.linked_battery_forecasts.get(self._ha_device_id)
        if forecast is None:
            return {
                "status": "no_data",
                "ha_device_id": self._ha_device_id,
            }
        return {
            "status": forecast.status,
            "ha_device_id": forecast.ha_device_id,
            "homebox_id": forecast.hb_item_id,
            "battery_entity_id": forecast.battery_entity_id,
            "battery_now": forecast.current,
            "battery_1d_ago": forecast.day_ago,
            "battery_7d_ago": forecast.week_ago,
            "battery_30d_ago": forecast.month_ago,
            "drain_rate_per_day": forecast.drain_rate_per_day,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._resolve_native_value()
        self.async_write_ha_state()
