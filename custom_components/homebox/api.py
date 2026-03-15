"""HomeBox API client."""

from __future__ import annotations

from http import HTTPStatus
import mimetypes
from pathlib import PurePosixPath
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, FormData
from yarl import URL

from homeassistant.util.network import normalize_url

from .const import API_BASE_PATH, LINK_TAG_NAME
from .item_fields import (
    build_item_update_payload,
    extract_item_fields,
    merge_backlink_field,
)
from .models import HomeBoxGroupStatistics, HomeBoxItemSummary

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


def normalize_homebox_host(host: str) -> str:
    """Normalize host to an absolute URL without a trailing slash."""
    candidate = host.strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    return normalize_url(candidate).rstrip("/")


class HomeBoxApiError(Exception):
    """Base HomeBox API error."""


class HomeBoxAuthenticationError(HomeBoxApiError):
    """HomeBox authentication failed."""


class HomeBoxConnectionError(HomeBoxApiError):
    """Connection to HomeBox failed."""


class HomeBoxInvalidImageUrlError(HomeBoxApiError):
    """Provided image URL is invalid."""


class HomeBoxImageDownloadError(HomeBoxApiError):
    """Image download from URL failed."""


class HomeBoxImageTooLargeError(HomeBoxApiError):
    """Downloaded image exceeds allowed size."""


class HomeBoxImageContentTypeError(HomeBoxApiError):
    """Downloaded file is not an image."""


class HomeBoxApiClient:
    """Client for HomeBox API."""

    def __init__(self, host: str, session: ClientSession) -> None:
        """Initialize the HomeBox API client."""
        self._host = normalize_homebox_host(host)
        self._api_url = URL(self._host).join(URL(API_BASE_PATH + "/"))
        self._session = session
        self._token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """Return if the client currently has an auth token."""
        return self._token is not None

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

    async def _async_request_json(
        self,
        method: str,
        path: str,
        *,
        auth_required: bool,
        error_context: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        include_auth_detail: bool = False,
    ) -> Any:
        """Execute a JSON request and map errors consistently."""
        url = self._api_url.join(URL(path))
        headers = self._build_auth_headers() if auth_required else None

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=payload,
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN) or (
                    include_auth_detail and response.status == HTTPStatus.BAD_REQUEST
                ):
                    if include_auth_detail:
                        detail = await self._extract_error_detail(response)
                        raise HomeBoxAuthenticationError(
                            f"Status {response.status}: {detail}"
                        )
                    raise HomeBoxAuthenticationError

                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox {error_context} failed with status {response.status}"
                    )

                return await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

    @staticmethod
    def _parse_group_statistics(payload: dict[str, Any]) -> HomeBoxGroupStatistics:
        """Validate and normalize HomeBox group statistics payload."""
        total_items = payload.get("totalItems")
        if not isinstance(total_items, int):
            raise HomeBoxApiError("HomeBox statistics response missing totalItems")

        total_locations = payload.get("totalLocations")
        if not isinstance(total_locations, int):
            raise HomeBoxApiError("HomeBox statistics response missing totalLocations")

        total_item_price = payload.get("totalItemPrice")
        if not isinstance(total_item_price, int | float):
            raise HomeBoxApiError("HomeBox statistics response missing totalItemPrice")

        return HomeBoxGroupStatistics(
            total_items=total_items,
            total_locations=total_locations,
            total_value=float(total_item_price),
        )

    @staticmethod
    def _parse_item_summary(payload: dict[str, Any]) -> HomeBoxItemSummary | None:
        """Parse and normalize a HomeBox item summary payload."""
        item_id = payload.get("id")
        if not isinstance(item_id, str):
            return None

        name = payload.get("name")
        if not isinstance(name, str) or not name:
            name = "Unknown"

        fields = payload.get("fields")
        normalized_fields: list[dict[str, Any]] | None = None
        if isinstance(fields, list):
            normalized_fields = [field for field in fields if isinstance(field, dict)]

        return HomeBoxItemSummary(item_id=item_id, name=name, fields=normalized_fields)

    async def async_authenticate(self, username: str, password: str) -> None:
        """Authenticate with HomeBox and store bearer token."""
        payload = {
            "username": username,
            "password": password,
            "stayLoggedIn": True,
        }

        response_data = await self._async_request_json(
            "POST",
            "v1/users/login",
            auth_required=False,
            error_context="login request",
            payload=payload,
            include_auth_detail=True,
        )

        if not isinstance(response_data, dict):
            raise HomeBoxApiError("HomeBox login response is invalid")

        token = response_data.get("token")
        if not isinstance(token, str) or not token:
            raise HomeBoxApiError("HomeBox token missing in login response")

        self._token = self._normalize_token(token)

    async def async_get_total_items(self) -> int:
        """Return total item count from HomeBox group statistics."""
        return (await self.async_get_group_statistics()).total_items

    async def async_get_group_statistics(self) -> HomeBoxGroupStatistics:
        """Return basic group statistics from HomeBox."""
        response_data = await self._async_request_json(
            "GET",
            "v1/groups/statistics",
            auth_required=True,
            error_context="statistics request",
        )
        if not isinstance(response_data, dict):
            raise HomeBoxApiError("HomeBox statistics response is invalid")
        return self._parse_group_statistics(response_data)

    async def async_get_tags(self) -> list[dict[str, Any]]:
        """Return all HomeBox tags."""
        data = await self._async_request_json(
            "GET",
            "v1/tags",
            auth_required=True,
            error_context="tags request",
        )
        if not isinstance(data, list):
            raise HomeBoxApiError("HomeBox tags response is not a list")
        return [item for item in data if isinstance(item, dict)]

    async def async_get_locations(self) -> list[dict[str, Any]]:
        """Return all HomeBox locations."""
        data = await self._async_request_json(
            "GET",
            "v1/locations",
            auth_required=True,
            error_context="locations request",
        )
        if not isinstance(data, list):
            raise HomeBoxApiError("HomeBox locations response is not a list")
        return [item for item in data if isinstance(item, dict)]

    async def async_create_location(self, name: str) -> dict[str, Any]:
        """Create a HomeBox location."""
        data = await self._async_request_json(
            "POST",
            "v1/locations",
            auth_required=True,
            error_context="create location",
            payload={"name": name, "description": "", "parentId": None},
        )
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

    async def async_ensure_locations_by_name(self, names: list[str]) -> dict[str, str]:
        """Ensure multiple HomeBox locations exist and return name->id mapping."""
        normalized_names = [
            " ".join(name.split()).strip()
            for name in names
            if isinstance(name, str) and name.strip()
        ]
        if not normalized_names:
            return {}

        by_casefold: dict[str, str] = {
            normalized_name.casefold(): normalized_name
            for normalized_name in normalized_names
        }
        target_casefolds = set(by_casefold)
        location_map: dict[str, str] = {}

        locations = await self.async_get_locations()
        for location in locations:
            location_name = location.get("name")
            location_id = location.get("id")
            if not isinstance(location_name, str) or not isinstance(location_id, str):
                continue
            casefold_name = location_name.casefold()
            if casefold_name in target_casefolds and casefold_name not in location_map:
                location_map[casefold_name] = location_id

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
        data = await self._async_request_json(
            "POST",
            "v1/tags",
            auth_required=True,
            error_context="create tag",
            payload={"name": name},
        )
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

    async def async_get_hb_items_by_tag(self, tag_id: str) -> list[HomeBoxItemSummary]:
        """Return all HomeBox items for a given tag."""
        page = 1
        page_size = 100
        total: int | None = None
        items: list[HomeBoxItemSummary] = []

        while total is None or len(items) < total:
            page_data = await self._async_request_json(
                "GET",
                "v1/items",
                auth_required=True,
                error_context="items request",
                params={"tags": [tag_id], "page": page, "pageSize": page_size},
            )
            if not isinstance(page_data, dict):
                raise HomeBoxApiError("HomeBox items response is invalid")

            page_items = page_data.get("items")
            total_value = page_data.get("total")
            if not isinstance(page_items, list) or not isinstance(total_value, int):
                raise HomeBoxApiError("HomeBox items response missing items/total")

            for page_item in page_items:
                if not isinstance(page_item, dict):
                    continue
                if parsed := self._parse_item_summary(page_item):
                    items.append(parsed)

            total = total_value
            if not page_items:
                break
            page += 1

        return items

    async def async_get_hb_item(self, hb_item_id: str) -> dict[str, Any]:
        """Return full HomeBox item."""
        data = await self._async_request_json(
            "GET",
            f"v1/items/{hb_item_id}",
            auth_required=True,
            error_context="item request",
        )
        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox item response is invalid")
        return data

    async def async_update_hb_item(
        self, hb_item_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a HomeBox item with PUT payload."""
        data = await self._async_request_json(
            "PUT",
            f"v1/items/{hb_item_id}",
            auth_required=True,
            error_context="item update",
            payload=payload,
        )
        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox item update response is invalid")
        return data

    async def async_delete_hb_item(self, hb_item_id: str) -> None:
        """Delete a HomeBox item."""
        url = self._api_url.join(URL(f"v1/items/{hb_item_id}"))
        headers = self._build_auth_headers()
        try:
            async with self._session.delete(url, headers=headers) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status == HTTPStatus.NOT_FOUND:
                    return
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox delete item failed with status {response.status}"
                    )
        except ClientError as err:
            raise HomeBoxConnectionError from err

    async def async_create_hb_item(
        self,
        *,
        name: str,
        location_id: str | None,
        tag_ids: list[str] | None,
    ) -> str:
        """Create a HomeBox item and return its ID."""
        payload: dict[str, Any] = {"name": name}
        if location_id:
            payload["locationId"] = location_id
        if tag_ids:
            payload["tagIds"] = tag_ids

        data = await self._async_request_json(
            "POST",
            "v1/items",
            auth_required=True,
            error_context="create item",
            payload=payload,
        )
        if not isinstance(data, dict):
            raise HomeBoxApiError("HomeBox create item response is invalid")

        hb_item_id = data.get("id")
        if not isinstance(hb_item_id, str) or not hb_item_id:
            raise HomeBoxApiError("HomeBox create item response missing item ID")
        return hb_item_id

    async def async_update_hb_item_details(
        self,
        hb_item_id: str,
        *,
        name: str,
        description: str,
        manufacturer: str,
        model_number: str,
        serial_number: str,
        purchase_price: float,
        location_id: str | None,
    ) -> None:
        """Update editable HomeBox item details after creation."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = extract_item_fields(hb_item)
        payload = build_item_update_payload(hb_item, fields)
        payload["name"] = name
        payload["description"] = description
        payload["manufacturer"] = manufacturer
        payload["modelNumber"] = model_number
        payload["serialNumber"] = serial_number
        payload["purchasePrice"] = purchase_price
        if location_id:
            payload["locationId"] = location_id
        await self.async_update_hb_item(hb_item_id, payload)

    @staticmethod
    def _validate_image_url(image_url: str) -> URL:
        """Validate and normalize provided image URL."""
        normalized = image_url.strip()
        if not normalized:
            raise HomeBoxInvalidImageUrlError("Image URL is empty")
        url = URL(normalized)
        if url.scheme not in {"http", "https"}:
            raise HomeBoxInvalidImageUrlError("Image URL must use http or https")
        if not url.host:
            raise HomeBoxInvalidImageUrlError("Image URL must include a host")
        return url

    @staticmethod
    def _infer_image_name(image_url: URL, content_type: str | None) -> str:
        """Infer a safe filename for an image attachment upload."""
        path_name = PurePosixPath(image_url.path or "").name
        if path_name and "." in path_name:
            return path_name

        extension = ""
        if content_type:
            guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
            if guessed:
                extension = guessed
        if not extension:
            extension = ".jpg"
        return f"ha_import{extension}"

    async def async_add_hb_item_photo_from_url(
        self,
        hb_item_id: str,
        image_url: str,
    ) -> None:
        """Download an image URL and upload it as a HomeBox photo attachment."""
        url = self._validate_image_url(image_url)
        try:
            async with self._session.get(url) as response:
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxImageDownloadError(
                        f"Image download failed with status {response.status}"
                    )
                content_type = (response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("image/"):
                    raise HomeBoxImageContentTypeError(
                        "Image URL did not return an image content type"
                    )
                image_chunks = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    image_chunks.extend(chunk)
                    if len(image_chunks) > MAX_IMAGE_SIZE_BYTES:
                        raise HomeBoxImageTooLargeError("Image is larger than 10MB")
                image_data = bytes(image_chunks)
        except ClientError as err:
            raise HomeBoxImageDownloadError from err

        upload_url = self._api_url.join(URL(f"v1/items/{hb_item_id}/attachments"))
        headers = self._build_auth_headers()

        form = FormData()
        form.add_field(
            "file",
            image_data,
            filename=self._infer_image_name(url, content_type),
            content_type=content_type.split(";", 1)[0].strip(),
        )
        form.add_field("name", self._infer_image_name(url, content_type))
        form.add_field("type", "photo")
        form.add_field("primary", "true")

        try:
            async with self._session.post(upload_url, headers=headers, data=form) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise HomeBoxAuthenticationError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise HomeBoxApiError(
                        f"HomeBox item attachment upload failed with status {response.status}"
                    )
                await response.json()
        except ClientError as err:
            raise HomeBoxConnectionError from err

    async def async_set_hb_item_backlink(
        self, hb_item_id: str, ha_device_url: str
    ) -> None:
        """Set Home Assistant backlink custom field on a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = extract_item_fields(hb_item)
        merged_fields = merge_backlink_field(fields, ha_device_url)
        payload = build_item_update_payload(hb_item, merged_fields)
        await self.async_update_hb_item(hb_item_id, payload)

    async def async_clear_hb_item_backlink(self, hb_item_id: str) -> None:
        """Remove Home Assistant backlink custom field from a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = extract_item_fields(hb_item)
        merged_fields = merge_backlink_field(fields, None)
        payload = build_item_update_payload(hb_item, merged_fields)
        await self.async_update_hb_item(hb_item_id, payload)

    async def async_set_hb_item_location(self, hb_item_id: str, hb_location_id: str) -> None:
        """Set location for a HomeBox item."""
        hb_item = await self.async_get_hb_item(hb_item_id)
        fields = extract_item_fields(hb_item)
        payload = build_item_update_payload(hb_item, fields)
        payload["locationId"] = hb_location_id
        await self.async_update_hb_item(hb_item_id, payload)

    def get_hb_item_url(self, hb_item_id: str) -> str:
        """Build HomeBox web URL for a given item."""
        return f"{self._host.rstrip('/')}/item/{hb_item_id}"
