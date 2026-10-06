"""Conversation platform for the Praktor integration."""

from __future__ import annotations

import logging

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PraktorConfigEntry
from .api import (
    PraktorAuthError,
    PraktorClient,
    PraktorConnectionError,
    PraktorError,
    PraktorForbiddenError,
    PraktorTimeoutError,
)
from .const import CONF_AGENT, CONF_TIMEOUT, DEFAULT_AGENT, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: PraktorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Praktor conversation entity."""
    async_add_entities([PraktorConversationEntity(config_entry)])


class PraktorConversationEntity(conversation.ConversationEntity):
    """Conversation agent backed by a Praktor agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False

    def __init__(self, entry: PraktorConfigEntry) -> None:
        """Initialize the conversation entity."""
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Praktor",
            manufacturer="Praktor",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | str:
        """Return the languages this agent supports."""
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the user's message to Praktor and return the agent's reply."""
        client: PraktorClient = self._entry.runtime_data

        try:
            response = await client.async_chat(
                user_input.text,
                agent=self._entry.options.get(CONF_AGENT, DEFAULT_AGENT),
                agent_timeout=self._entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            )
        except PraktorAuthError as err:
            # Ask the user for a new token in the UI.
            self._entry.async_start_reauth(self.hass)
            return self._async_error_result(
                user_input,
                chat_log,
                "Praktor rejected the configured chat token.",
                err,
            )
        except PraktorForbiddenError as err:
            return self._async_error_result(
                user_input,
                chat_log,
                "This Praktor agent isn't allowed for the chat token.",
                err,
            )
        except PraktorTimeoutError as err:
            return self._async_error_result(
                user_input, chat_log, "Praktor timed out.", err
            )
        except PraktorConnectionError as err:
            return self._async_error_result(
                user_input, chat_log, "Can't reach Praktor.", err
            )
        except PraktorError as err:
            return self._async_error_result(
                user_input, chat_log, "Praktor returned an error.", err
            )

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=response.reply,
            )
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    def _async_error_result(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        message: str,
        error: PraktorError,
    ) -> conversation.ConversationResult:
        """Build an error result for a failed request.

        The error details are logged for troubleshooting; neither the chat
        token nor the raw response body is ever exposed to the user.
        """
        _LOGGER.warning("Error talking to Praktor: %s (%s)", message, error.message)

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
        )
