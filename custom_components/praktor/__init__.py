"""The Praktor integration.

Praktor is a chat service running on the local network. This integration
exposes it to Home Assistant as a conversation agent and as the
``praktor.ask`` service.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    PraktorAuthError,
    PraktorClient,
    PraktorConnectionError,
    PraktorError,
    PraktorForbiddenError,
    PraktorTimeoutError,
)
from .const import (
    ATTR_AGENT,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_MESSAGE,
    ATTR_TIMEOUT,
    CONF_AGENT,
    CONF_CHAT_TOKEN,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_AGENT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MAX_API_TIMEOUT,
    MIN_API_TIMEOUT,
    SERVICE_ASK,
)

PLATFORMS: list[Platform] = [Platform.CONVERSATION]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PraktorConfigEntry = ConfigEntry[PraktorClient]

SERVICE_ASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_AGENT): cv.string,
        vol.Optional(ATTR_TIMEOUT): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_API_TIMEOUT, max=MAX_API_TIMEOUT)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Praktor integration."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_ASK,
        partial(_async_handle_ask, hass),
        schema=SERVICE_ASK_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PraktorConfigEntry) -> bool:
    """Set up a Praktor config entry."""
    entry.runtime_data = PraktorClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_CHAT_TOKEN],
        timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PraktorConfigEntry) -> bool:
    """Unload a Praktor config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _async_get_entry(hass: HomeAssistant, entry_id: str | None) -> PraktorConfigEntry:
    """Return the config entry targeted by a service call.

    Raises:
        HomeAssistantError: If the entry is unknown or not loaded, or if no
            Praktor entry is loaded at all.
    """
    if entry_id is not None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
            )
        return entry

    loaded_entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not loaded_entries:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_config_entry",
        )
    return loaded_entries[0]


async def _async_handle_ask(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Send a message to Praktor and return the agent's reply."""
    entry = _async_get_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
    client: PraktorClient = entry.runtime_data

    try:
        response = await client.async_chat(
            call.data[ATTR_MESSAGE],
            agent=call.data.get(
                ATTR_AGENT, entry.options.get(CONF_AGENT, DEFAULT_AGENT)
            ),
            agent_timeout=call.data.get(ATTR_TIMEOUT),
        )
    except PraktorAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="auth_error",
        ) from err
    except PraktorForbiddenError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="forbidden",
        ) from err
    except PraktorTimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="timeout_error",
        ) from err
    except PraktorConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err
    except PraktorError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_error",
            translation_placeholders={"error": err.message},
        ) from err

    return {"agent": response.agent, "reply": response.reply}
