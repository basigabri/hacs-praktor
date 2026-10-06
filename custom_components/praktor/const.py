"""Constants for the Praktor integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "praktor"

# Configuration keys.
CONF_URL: Final = "url"
CONF_CHAT_TOKEN: Final = "chat_token"  # noqa: S105 - a config key name, not a secret
CONF_AGENT: Final = "agent"
CONF_TIMEOUT: Final = "timeout"

# Service names and fields.
SERVICE_ASK: Final = "ask"
ATTR_MESSAGE: Final = "message"
ATTR_AGENT: Final = "agent"
ATTR_TIMEOUT: Final = "timeout"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

# Defaults.
DEFAULT_URL: Final = "http://local-praktor:8080"
DEFAULT_AGENT: Final = ""
DEFAULT_TIMEOUT: Final = 120

# Range accepted by the config entry options and the options flow.
MIN_TIMEOUT: Final = 10
MAX_TIMEOUT: Final = 600

# Range accepted by Praktor's chat API itself.
MIN_API_TIMEOUT: Final = 1
MAX_API_TIMEOUT: Final = 600

# Extra seconds added on top of the requested agent timeout before the HTTP
# request itself is aborted.
REQUEST_TIMEOUT_MARGIN: Final = 15

# Path of the only endpoint the chat token grants access to.
CHAT_ENDPOINT: Final = "/api/chat"
