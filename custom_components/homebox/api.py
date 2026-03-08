"""HomeBox API client."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
from yarl import URL

from homeassistant.util.network import normalize_url

from .const import API_BASE_PATH


class HomeBoxApiError(Exception):
    """Base HomeBox API error."""


class HomeBoxAuthenticationError(HomeBoxApiError):
    """HomeBox authentication failed."""


class HomeBoxConnectionError(HomeBoxApiError):
    """Connection to HomeBox failed."""


class HomeBoxApiClient:
    """Client for HomeBox API."""

    def __init__(self, host: str, session: ClientSession) -> None:
        """Initialize the HomeBox API client."""
        normalized_host = self._normalize_host(host)
        self._api_url = URL(normalized_host).join(URL(API_BASE_PATH + "/"))
        self._session = session
        self._token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """Return if the client currently has an auth token."""
        return self._token is not None

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Normalize host to an absolute URL without a trailing slash."""
        candidate = host.strip()
        if "://" not in candidate:
            candidate = f"http://{candidate}"

        return normalize_url(candidate).rstrip("/")

    @staticmethod
    def _normalize_token(token: str) -> str:
        """Normalize token by removing a bearer prefix if present."""
        normalized = token.strip()
        if normalized.lower().startswith("bearer "):
            normalized = normalized[7:].strip()
        return normalized

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authenticated headers."""
        if self._token is None:
            raise HomeBoxAuthenticationError
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    async def _extract_error_detail(response: ClientResponse) -> str:
        """Extract a short and safe detail from an error response."""
        detail = ""
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    detail = value
                    break
        elif isinstance(payload, str):
            detail = payload

        if not detail:
            try:
                detail = await response.text()
            except (ValueError, TypeError):
                detail = ""

        detail = " ".join(detail.split()).strip()
        if not detail:
            return "No details returned by HomeBox."
        return detail[:200]

    async def async_authenticate(self, username: str, password: str) -> None:
        """Authenticate with HomeBox and store bearer token."""
        url = self._api_url.join(URL("v1/users/login"))
        payload: dict[str, Any] = {
            "username": username,
            "password": password,
            "stayLoggedIn": True,
        }

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status in (
                    HTTPStatus.UNAUTHORIZED,
                    HTTPStatus.FORBIDDEN,
                    HTTPStatus.BAD_REQUEST,
                ):
                    detail = await self._extract_error_detail(response)
                    raise HomeBoxAuthenticationError(
                        f"Status {response.status}: {detail}"
                    )
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox login request failed with status {response.status}"
                    )

                response_data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        token = response_data.get("token")
        if not isinstance(token, str) or not token:
            raise HomeBoxApiError("HomeBox token missing in login response")

        self._token = self._normalize_token(token)

    async def async_get_total_items(self) -> int:
        """Return total item count from HomeBox group statistics."""
        url = self._api_url.join(URL("v1/groups/statistics"))
        headers = self._build_auth_headers()

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        "HomeBox statistics request failed "
                        f"with status {response.status}"
                    )

                response_data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        total_items = response_data.get("totalItems")
        if not isinstance(total_items, int):
            raise HomeBoxApiError("HomeBox statistics response missing totalItems")

        return total_items

    async def async_get_group_statistics(self) -> dict[str, int | float]:
        """Return basic group statistics from HomeBox."""
        url = self._api_url.join(URL("v1/groups/statistics"))
        headers = self._build_auth_headers()

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        "HomeBox statistics request failed "
                        f"with status {response.status}"
                    )

                response_data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        total_items = response_data.get("totalItems")
        if not isinstance(total_items, int):
            raise HomeBoxApiError("HomeBox statistics response missing totalItems")

        total_locations = response_data.get("totalLocations")
        if not isinstance(total_locations, int):
            raise HomeBoxApiError("HomeBox statistics response missing totalLocations")

        total_item_price = response_data.get("totalItemPrice")
        if not isinstance(total_item_price, int | float):
            raise HomeBoxApiError("HomeBox statistics response missing totalItemPrice")

        return {
            "total_items": total_items,
            "total_locations": total_locations,
            "total_value": float(total_item_price),
        }
