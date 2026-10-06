"""Config flow for the Praktor integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    PraktorAuthError,
    PraktorClient,
    PraktorConnectionError,
    PraktorError,
    PraktorTimeoutError,
    normalize_url,
)
from .const import (
    CONF_AGENT,
    CONF_CHAT_TOKEN,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_AGENT,
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    DOMAIN,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def _chat_token_selector() -> TextSelector:
    """Return a selector for the chat token that masks the value."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _entry_title(url: str) -> str:
    """Return a human readable title for a config entry."""
    return f"Praktor ({urlsplit(url).netloc})"


async def _async_validate_input(
    hass: HomeAssistant,
    url: str,
    chat_token: str,
    agent_timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Validate the URL and chat token against Praktor."""
    client = PraktorClient(
        async_get_clientsession(hass), url, chat_token, timeout=agent_timeout
    )
    await client.async_validate()


class PraktorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Praktor config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PraktorOptionsFlow:
        """Return the options flow for a Praktor config entry."""
        return PraktorOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step of adding a Praktor instance."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError as err:
                _LOGGER.debug("Rejected invalid Praktor URL: %s", err)
                errors[CONF_URL] = "invalid_url"
            else:
                try:
                    await _async_validate_input(
                        self.hass, url, user_input[CONF_CHAT_TOKEN]
                    )
                except PraktorAuthError:
                    errors["base"] = "invalid_auth"
                except (PraktorConnectionError, PraktorTimeoutError):
                    errors["base"] = "cannot_connect"
                except PraktorError as err:
                    _LOGGER.warning(
                        "Unexpected error while validating Praktor: %s", err.message
                    )
                    errors["base"] = "unknown"
                except Exception:
                    _LOGGER.exception("Unexpected exception while validating Praktor")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=_entry_title(url),
                        data={
                            CONF_URL: url,
                            CONF_CHAT_TOKEN: user_input[CONF_CHAT_TOKEN],
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL,
                        default=(user_input or {}).get(CONF_URL, DEFAULT_URL),
                    ): cv.string,
                    vol.Required(CONF_CHAT_TOKEN): _chat_token_selector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauthentication request for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user for a new chat token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        url: str = entry.data[CONF_URL]

        if user_input is not None:
            try:
                await _async_validate_input(
                    self.hass,
                    url,
                    user_input[CONF_CHAT_TOKEN],
                    agent_timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                )
            except PraktorAuthError:
                errors["base"] = "invalid_auth"
            except (PraktorConnectionError, PraktorTimeoutError):
                errors["base"] = "cannot_connect"
            except PraktorError as err:
                _LOGGER.warning(
                    "Unexpected error while validating Praktor: %s", err.message
                )
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception while validating Praktor")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_CHAT_TOKEN: user_input[CONF_CHAT_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CHAT_TOKEN): _chat_token_selector()}
            ),
            errors=errors,
            description_placeholders={CONF_URL: url},
        )


class PraktorOptionsFlow(OptionsFlowWithReload):
    """Handle the Praktor options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the Praktor options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_AGENT: user_input.get(CONF_AGENT, DEFAULT_AGENT).strip(),
                    CONF_TIMEOUT: int(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # suggested_value, not default: a default would come
                    # back when the user clears the field.
                    vol.Optional(
                        CONF_AGENT,
                        description={
                            "suggested_value": options.get(CONF_AGENT, DEFAULT_AGENT)
                        },
                    ): cv.string,
                    vol.Optional(
                        CONF_TIMEOUT,
                        description={
                            "suggested_value": options.get(
                                CONF_TIMEOUT, DEFAULT_TIMEOUT
                            )
                        },
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_TIMEOUT,
                            max=MAX_TIMEOUT,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
        )
