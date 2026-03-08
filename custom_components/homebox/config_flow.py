"""Config flow for the HomeBox integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.network import normalize_url

from .api import HomeBoxApiClient, HomeBoxAuthenticationError, HomeBoxConnectionError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    api = HomeBoxApiClient(data[CONF_HOST], async_get_clientsession(hass))

    try:
        await api.async_authenticate(data[CONF_USERNAME], data[CONF_PASSWORD])
        await api.async_get_total_items()
    except HomeBoxAuthenticationError as err:
        raise InvalidAuth(str(err)) from err
    except HomeBoxConnectionError as err:
        raise CannotConnect from err

    normalized_host = normalize_host(data[CONF_HOST])
    return {"title": URL(normalized_host).host or normalized_host}


def normalize_host(host: str) -> str:
    """Normalize host to absolute URL string."""
    host = host.strip()
    if "://" not in host:
        host = f"http://{host}"
    return normalize_url(host).rstrip("/")


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeBox."""

    VERSION = 1
    _auth_error_detail: str = "No authentication attempt yet."

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        suggested_values: dict[str, Any] = {}
        if user_input is not None:
            normalized_host = normalize_host(user_input[CONF_HOST])
            try:
                user_input[CONF_HOST] = normalized_host
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth as err:
                errors["base"] = "invalid_auth_homebox"
                self._auth_error_detail = err.detail
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(normalized_host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)
            suggested_values = {
                CONF_HOST: user_input.get(CONF_HOST, ""),
                CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
            }

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors,
            description_placeholders={"auth_error_detail": self._auth_error_detail},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

    def __init__(self, detail: str = "No details returned by HomeBox.") -> None:
        """Initialize InvalidAuth."""
        self.detail = detail
