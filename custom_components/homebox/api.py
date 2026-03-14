"""HomeBox API client."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
from yarl import URL

from homeassistant.util.network import normalize_url

from .const import API_BASE_PATH, LINK_BACKLINK_FIELD_NAME, LINK_TAG_NAME


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

    async def async_get_tags(self) -> list[dict[str, Any]]:
        """Return all HomeBox tags."""
        url = self._api_url.join(URL("v1/tags"))
        headers = self._build_auth_headers()

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox tags request failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, list):
            raise HomeBoxApiError("HomeBox tags response is not a list")
        return data

    async def async_get_locations(self) -> list[dict[str, Any]]:
        """Return all HomeBox locations."""
        url = self._api_url.join(URL("v1/locations"))
        headers = self._build_auth_headers()

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox locations request failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, list):
            raise HomeBoxApiError("HomeBox locations response is not a list")
        return data

    async def async_create_location(self, name: str) -> dict[str, Any]:
        """Create a HomeBox location."""
        url = self._api_url.join(URL("v1/locations"))
        headers = self._build_auth_headers()

        try:
            async with self._session.post(
                url,
                headers=headers,
                json={
                    "name": name,
                    "description": "",
                    "parentId": None,
                },
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox create location failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox create location response is invalid")
        return data

    async def async_ensure_location_by_name(self, name: str) -> str:
        """Ensure a HomeBox location with matching name exists and return its ID."""
        normalized_name = " ".join(name.split()).strip()
        if not normalized_name:
            raise HomeBoxApiError("HomeBox location name must not be empty")

        locations = await self.async_get_locations()
        lookup_name = normalized_name.casefold()
        for location in locations:
            location_name = location.get("name")
            location_id = location.get("id")
            if (
                isinstance(location_name, str)
                and isinstance(location_id, str)
                and location_name.casefold() == lookup_name
            ):
                return location_id

        created = await self.async_create_location(normalized_name)
        location_id = created.get("id")
        if not isinstance(location_id, str):
            raise HomeBoxApiError("Created HomeBox location has no ID")
        return location_id

    async def async_ensure_locations_by_name(
        self, names: list[str]
    ) -> dict[str, str]:
        """Ensure multiple HomeBox locations exist and return name->id mapping."""
        normalized_names = [
            " ".join(name.split()).strip()
            for name in names
            if isinstance(name, str) and name.strip()
        ]
        if not normalized_names:
            return {}

        by_casefold: dict[str, str] = {}
        for normalized_name in normalized_names:
            by_casefold[normalized_name.casefold()] = normalized_name

        target_casefolds = set(by_casefold)
        location_map: dict[str, str] = {}

        locations = await self.async_get_locations()
        for location in locations:
            location_name = location.get("name")
            location_id = location.get("id")
            if not isinstance(location_name, str) or not isinstance(location_id, str):
                continue
            key = location_name.casefold()
            if key in target_casefolds and key not in location_map:
                location_map[key] = location_id

        for casefold_name in target_casefolds:
            if casefold_name in location_map:
                continue
            normalized_name = by_casefold[casefold_name]
            created = await self.async_create_location(normalized_name)
            created_id = created.get("id")
            if not isinstance(created_id, str):
                raise HomeBoxApiError("Created HomeBox location has no ID")
            location_map[casefold_name] = created_id

        return {
            by_casefold[casefold_name]: location_id
            for casefold_name, location_id in location_map.items()
            if casefold_name in by_casefold
        }

    async def async_create_tag(self, name: str) -> dict[str, Any]:
        """Create a HomeBox tag."""
        url = self._api_url.join(URL("v1/tags"))
        headers = self._build_auth_headers()

        try:
            async with self._session.post(
                url, headers=headers, json={"name": name}
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox create tag failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox create tag response is invalid")
        return data

    async def async_ensure_link_tag(self) -> str:
        """Ensure the HomeAssistant tag exists and return its ID."""
        tags = await self.async_get_tags()
        for tag in tags:
            if tag.get("name") == LINK_TAG_NAME and isinstance(tag.get("id"), str):
                return tag["id"]

        created = await self.async_create_tag(LINK_TAG_NAME)
        tag_id = created.get("id")
        if not isinstance(tag_id, str):
            raise HomeBoxApiError("Created HomeAssistant tag has no ID")
        return tag_id

    async def async_get_hb_items_by_tag(self, tag_id: str) -> list[dict[str, Any]]:
        """Return all HomeBox items for a given tag."""
        url = self._api_url.join(URL("v1/items"))
        headers = self._build_auth_headers()
        page = 1
        page_size = 100
        total = None
        items: list[dict[str, Any]] = []

        while total is None or len(items) < total:
            params: dict[str, Any] = {
                "tags": [tag_id],
                "page": page,
                "pageSize": page_size,
            }
            try:
                async with self._session.get(
                    url, headers=headers, params=params
                ) as response:
                    if response.status in (
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                    ):
                        raise HomeBoxAuthenticationError
                    if response.status >= HTTPStatus.BAD_REQUEST:
                        raise HomeBoxApiError(
                            f"HomeBox items request failed with status {response.status}"
                        )
                    data = await response.json()
            except ClientError as err:
                raise HomeBoxConnectionError from err

            if not isinstance(data, dict):
                raise HomeBoxApiError("HomeBox items response is invalid")

            page_items = data.get("items")
            total_value = data.get("total")
            if not isinstance(page_items, list) or not isinstance(total_value, int):
                raise HomeBoxApiError("HomeBox items response missing items/total")

            items.extend(item for item in page_items if isinstance(item, dict))

            total = total_value
            if not page_items:
                break
            page += 1

        return items

    async def async_get_hb_item(self, hb_item_id: str) -> dict[str, Any]:
        """Return full HomeBox item."""
        url = self._api_url.join(URL(f"v1/items/{hb_item_id}"))
        headers = self._build_auth_headers()

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox item request failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox item response is invalid")
        return data

    @staticmethod
    def _extract_item_fields(hb_item: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract fields from HomeBox item."""
        fields = hb_item.get("fields")
        if not isinstance(fields, list):
            return []
        return [field for field in fields if isinstance(field, dict)]

    @staticmethod
    def _merge_backlink_field(
        fields: list[dict[str, Any]], backlink_url: str | None
    ) -> list[dict[str, Any]]:
        """Merge Home Assistant backlink custom field into fields list."""
        merged: list[dict[str, Any]] = []
        found = False

        for field in fields:
            name = field.get("name")
            if name == LINK_BACKLINK_FIELD_NAME:
                found = True
                if backlink_url:
                    merged.append(
                        {
                            "id": field.get("id"),
                            "type": "text",
                            "name": LINK_BACKLINK_FIELD_NAME,
                            "textValue": backlink_url,
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

    @staticmethod
    def _build_item_update_payload(
        hb_item: dict[str, Any], fields: list[dict[str, Any]]
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
            "syncChildItemsLocations": bool(
                hb_item.get("syncChildItemsLocations", False)
            ),
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

    async def async_update_hb_item(
        self, hb_item_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a HomeBox item with PUT payload."""
        url = self._api_url.join(URL(f"v1/items/{hb_item_id}"))
        headers = self._build_auth_headers()

        try:
            async with self._session.put(url, headers=headers, json=payload) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox item update failed with status {response.status}"
                    )
                data = await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox item update response is invalid")
        return data

    async def async_set_hb_item_backlink(
        self, hb_item_id: str, ha_device_url: str
    ) -> None:
        """Set Home Assistant backlink custom field on a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = self._extract_item_fields(hb_item)
        merged_fields = self._merge_backlink_field(fields, ha_device_url)
        payload = self._build_item_update_payload(hb_item, merged_fields)
        await self.async_update_hb_item(hb_item_id, payload)

    async def async_clear_hb_item_backlink(self, hb_item_id: str) -> None:
        """Remove Home Assistant backlink custom field from a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = self._extract_item_fields(hb_item)
        merged_fields = self._merge_backlink_field(fields, None)
        payload = self._build_item_update_payload(hb_item, merged_fields)
        await self.async_update_hb_item(hb_item_id, payload)

    async def async_set_hb_item_location(
        self, hb_item_id: str, hb_location_id: str
    ) -> None:
        """Set location for a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = self._extract_item_fields(hb_item)
        payload = self._build_item_update_payload(hb_item, fields)
        payload["locationId"] = hb_location_id
        await self.async_update_hb_item(hb_item_id, payload)

    def get_hb_item_url(self, hb_item_id: str) -> str:
        """Build HomeBox web URL for a given item."""
        base_url = str(self._api_url).removesuffix(API_BASE_PATH + "/")
        return f"{base_url.rstrip('/')}/item/{hb_item_id}"
