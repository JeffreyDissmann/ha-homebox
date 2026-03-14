"""Typed models for HomeBox API data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class HomeBoxGroupStatistics:
    """Normalized group statistics returned by HomeBox."""

    total_items: int
    total_locations: int
    total_value: float


@dataclass(slots=True, frozen=True)
class HomeBoxItemSummary:
    """Summary item returned by HomeBox list endpoints."""

    item_id: str
    name: str
    fields: list[dict[str, Any]] | None = None
