"""Sensor platform for HomeBox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HomeBoxConfigEntry, HomeBoxDataUpdateCoordinator


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up HomeBox sensor entities from config entry."""
    async_add_entities(
        HomeBoxStatisticsSensor(
            entry.runtime_data,
            entry.entry_id,
            entry.title,
            entry.data[CONF_HOST],
            description,
        )
        for description in SENSOR_DESCRIPTIONS
    )


class HomeBoxStatisticsSensor(
    CoordinatorEntity[HomeBoxDataUpdateCoordinator], SensorEntity
):
    """HomeBox statistics sensor."""

    entity_description: HomeBoxSensorEntityDescription

    def __init__(
        self,
        coordinator: HomeBoxDataUpdateCoordinator,
        config_entry_id: str,
        config_entry_title: str,
        host: str,
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
            name=config_entry_title or "HomeBox",
            manufacturer="HomeBox",
            configuration_url=host,
        )
        self._attr_has_entity_name = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = getattr(
            self.coordinator.data, self.entity_description.value_key
        )
        self.async_write_ha_state()
