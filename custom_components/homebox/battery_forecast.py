"""Battery history and depletion forecast for linked HomeBox devices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .linking import get_link_maps

LOOKBACK_DAY = 1
LOOKBACK_WEEK = 7
LOOKBACK_MONTH = 30
MIN_DRAIN_RATE_PER_DAY = 0.01

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LinkedBatteryForecast:
    """Battery forecast data for one linked Home Assistant device."""

    ha_device_id: str
    hb_item_id: str
    battery_entity_id: str | None
    current: float | None
    day_ago: float | None
    week_ago: float | None
    month_ago: float | None
    drain_rate_per_day: float | None
    estimated_depletion_at: datetime | None
    status: str


async def async_collect_linked_battery_forecasts(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, LinkedBatteryForecast]:
    """Collect linked battery snapshots and forecast for all linked devices."""
    ha_device_to_hb_item, _ = get_link_maps(entry)
    if not ha_device_to_hb_item:
        _LOGGER.debug("Battery forecast: no linked devices found")
        return {}

    entity_registry = er.async_get(hass)
    forecasts: dict[str, LinkedBatteryForecast] = {}
    recorder_available = "recorder" in hass.config.components

    for ha_device_id, hb_item_id in ha_device_to_hb_item.items():
        battery_entity_id = _find_primary_battery_entity_id(entity_registry, ha_device_id)
        if battery_entity_id is None:
            _LOGGER.debug(
                "Battery forecast: no battery entity for linked device %s (hb_item=%s)",
                ha_device_id,
                hb_item_id,
            )
            forecasts[ha_device_id] = LinkedBatteryForecast(
                ha_device_id=ha_device_id,
                hb_item_id=hb_item_id,
                battery_entity_id=None,
                current=None,
                day_ago=None,
                week_ago=None,
                month_ago=None,
                drain_rate_per_day=None,
                estimated_depletion_at=None,
                status="no_battery_entity",
            )
            continue

        current = _get_current_battery_value(hass, battery_entity_id)
        if not recorder_available:
            _LOGGER.debug(
                "Battery forecast: recorder unavailable for device %s entity %s",
                ha_device_id,
                battery_entity_id,
            )
            forecasts[ha_device_id] = LinkedBatteryForecast(
                ha_device_id=ha_device_id,
                hb_item_id=hb_item_id,
                battery_entity_id=battery_entity_id,
                current=current,
                day_ago=None,
                week_ago=None,
                month_ago=None,
                drain_rate_per_day=None,
                estimated_depletion_at=None,
                status="recorder_unavailable",
            )
            continue

        history_points = await _async_get_battery_history_points(hass, battery_entity_id)
        if not history_points:
            _LOGGER.debug(
                "Battery forecast: no recorder history for device %s entity %s",
                ha_device_id,
                battery_entity_id,
            )
            forecasts[ha_device_id] = LinkedBatteryForecast(
                ha_device_id=ha_device_id,
                hb_item_id=hb_item_id,
                battery_entity_id=battery_entity_id,
                current=current,
                day_ago=None,
                week_ago=None,
                month_ago=None,
                drain_rate_per_day=None,
                estimated_depletion_at=None,
                status="no_history",
            )
            continue

        now = dt_util.utcnow()
        day_ago = _value_at_or_before(history_points, now - timedelta(days=LOOKBACK_DAY))
        week_ago = _value_at_or_before(history_points, now - timedelta(days=LOOKBACK_WEEK))
        month_ago = _value_at_or_before(history_points, now - timedelta(days=LOOKBACK_MONTH))
        if current is None:
            current = _value_at_or_before(history_points, now)

        drain_rate, depletion_at = _estimate_depletion_date(
            now=now,
            current=current,
            day_ago=day_ago,
            week_ago=week_ago,
            month_ago=month_ago,
        )
        status = "ok" if depletion_at is not None else "insufficient_trend"
        _LOGGER.debug(
            "Battery forecast: device %s hb_item=%s entity=%s now=%s 1d=%s 7d=%s 30d=%s rate=%s status=%s depletion=%s",
            ha_device_id,
            hb_item_id,
            battery_entity_id,
            current,
            day_ago,
            week_ago,
            month_ago,
            drain_rate,
            status,
            depletion_at,
        )

        forecasts[ha_device_id] = LinkedBatteryForecast(
            ha_device_id=ha_device_id,
            hb_item_id=hb_item_id,
            battery_entity_id=battery_entity_id,
            current=current,
            day_ago=day_ago,
            week_ago=week_ago,
            month_ago=month_ago,
            drain_rate_per_day=drain_rate,
            estimated_depletion_at=depletion_at,
            status=status,
        )

    return forecasts


def _find_primary_battery_entity_id(
    entity_registry: er.EntityRegistry, ha_device_id: str
) -> str | None:
    """Find a battery sensor entity linked to the given HA device."""
    candidates: list[tuple[int, str]] = []
    for entry in er.async_entries_for_device(
        entity_registry,
        ha_device_id,
        include_disabled_entities=False,
    ):
        battery_device_class = entry.options.get("sensor", {}).get(ATTR_DEVICE_CLASS)
        is_battery_device_class = any(
            device_class == SensorDeviceClass.BATTERY
            for device_class in (
                entry.device_class,
                entry.original_device_class,
                battery_device_class,
            )
        )
        # Prefer real percentage battery sensors.
        if entry.domain == "sensor" and (is_battery_device_class or "battery" in entry.entity_id):
            score = 100
            if "battery_plus_low" in entry.entity_id or "low" in entry.entity_id:
                score -= 20
            if "battery_plus" in entry.entity_id:
                score -= 5
            if entry.entity_id.endswith("_battery"):
                score += 10
            candidates.append((score, entry.entity_id))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][1]


def _get_current_battery_value(hass: HomeAssistant, entity_id: str) -> float | None:
    """Get current battery value from state machine."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return _parse_battery_state(state.state)


async def _async_get_battery_history_points(
    hass: HomeAssistant, entity_id: str
) -> list[tuple[datetime, float]]:
    """Fetch battery history points for the past month."""
    end = dt_util.utcnow()
    start = end - timedelta(days=LOOKBACK_MONTH + 2)

    result = await get_instance(hass).async_add_executor_job(
        history.get_significant_states,
        hass,
        start,
        end,
        [entity_id],
        None,
        True,
        False,
        False,
        True,
        False,
    )
    raw_states = result.get(entity_id, [])
    points: list[tuple[datetime, float]] = []
    for state in raw_states:
        if not isinstance(state, State):
            continue
        value = _parse_battery_state(state.state)
        if value is None:
            continue
        points.append((state.last_updated, value))
    points.sort(key=lambda item: item[0])
    return points


def _parse_battery_state(raw_state: str) -> float | None:
    """Parse battery sensor state into a bounded percentage value."""
    try:
        value = float(raw_state)
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 100:
        return None
    return value


def _value_at_or_before(
    points: list[tuple[datetime, float]], target_time: datetime
) -> float | None:
    """Return last known value at or before target time."""
    value: float | None = None
    for point_time, point_value in points:
        if point_time > target_time:
            break
        value = point_value
    return value


def _estimate_depletion_date(
    *,
    now: datetime,
    current: float | None,
    day_ago: float | None,
    week_ago: float | None,
    month_ago: float | None,
) -> tuple[float | None, datetime | None]:
    """Estimate battery depletion date using linear trend across snapshots."""
    samples: list[tuple[float, float]] = []
    if current is not None:
        samples.append((0.0, current))
    if day_ago is not None:
        samples.append((-1.0, day_ago))
    if week_ago is not None:
        samples.append((-7.0, week_ago))
    if month_ago is not None:
        samples.append((-30.0, month_ago))
    if len(samples) < 2 or current is None:
        return None, None

    xs = [sample[0] for sample in samples]
    ys = [sample[1] for sample in samples]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return None, None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / denominator
    if slope >= -MIN_DRAIN_RATE_PER_DAY:
        return slope, None

    days_until_zero = -current / slope
    if days_until_zero <= 0:
        return slope, now
    depletion_at = now + timedelta(days=days_until_zero)
    return slope, depletion_at
