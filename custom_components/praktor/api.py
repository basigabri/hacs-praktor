"""Async client for Praktor's chat API.

The chat token only grants access to ``POST /api/chat``, so this module
deliberately talks to that single endpoint and nothing else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import (
    CHAT_ENDPOINT,
    DEFAULT_TIMEOUT,
    MAX_API_TIMEOUT,
    MIN_API_TIMEOUT,
    REQUEST_TIMEOUT_MARGIN,
)

_LOGGER = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def normalize_url(url: str) -> str:
    """Validate and normalize a Praktor base URL.

    Trailing slashes are stripped. The URL must use http or https, must not
    carry credentials, and must not contain a query string or fragment.

    Returns:
        The normalized base URL, without a trailing slash.

    Raises:
        ValueError: If the URL cannot be used as a Praktor base URL.
    """
    normalized = url.strip().rstrip("/")
    if not normalized:
        raise ValueError("URL is empty")

    parsed = urlsplit(normalized)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("URL must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not contain a query string or fragment")

    # Scheme and host are case-insensitive: lowercase them so the same server
    # can't be added twice under different spellings.
    return parsed._replace(
        scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()
    ).geturl()


def _clamp_timeout(timeout: float) -> int:
    """Clamp a timeout to the whole-second range Praktor's chat API accepts.

    Home Assistant's number selector stores floats (120.0); Praktor's API
    rejects anything that isn't an integer, so always send an int.
    """
    return max(MIN_API_TIMEOUT, min(MAX_API_TIMEOUT, int(timeout)))


@dataclass(frozen=True, slots=True, kw_only=True)
class PraktorResponse:
    """A successful reply from Praktor."""

    agent: str
    reply: str


class PraktorError(Exception):
    """Base exception for all Praktor errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialize the error with Praktor's error text and HTTP status."""
        super().__init__(message)
        self.message = message
        self.status = status


class PraktorAuthError(PraktorError):
    """The chat token was rejected by Praktor (401)."""


class PraktorForbiddenError(PraktorError):
    """The token is valid but may not reach the requested agent (403)."""


class PraktorTimeoutError(PraktorError):
    """Praktor did not reply in time."""


class PraktorConnectionError(PraktorError):
    """Praktor could not be reached."""


class PraktorClient:
    """Client for Praktor's chat API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        chat_token: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        ``url`` is expected to be normalized with :func:`normalize_url`.
        """
        self._session = session
        self._url = url
        self._chat_token = chat_token
        self._default_timeout = _clamp_timeout(timeout)
        self._chat_url = f"{url}{CHAT_ENDPOINT}"

    def __repr__(self) -> str:
        """Return a representation that never leaks the chat token."""
        return f"PraktorClient(url={self._url!r})"

    @property
    def _headers(self) -> dict[str, str]:
        """Return the request headers, including the bearer token."""
        return {
            "Authorization": f"Bearer {self._chat_token}",
            "Content-Type": "application/json",
        }

    async def async_chat(
        self,
        message: str,
        agent: str | None = None,
        agent_timeout: float | None = None,
    ) -> PraktorResponse:
        """Send a chat message to Praktor and return the agent's reply.

        Args:
            message: The message to send to the agent.
            agent: Optional agent name. Praktor picks the first allowed agent
                when this is empty or omitted.
            agent_timeout: How long Praktor may wait for the agent, in
                seconds; falls back to the timeout configured on the entry.

        Raises:
            PraktorAuthError: The token was rejected (401).
            PraktorForbiddenError: The agent isn't allowed for the token (403).
            PraktorTimeoutError: The agent did not reply in time (504).
            PraktorConnectionError: Praktor could not be reached.
            PraktorError: Praktor returned any other error.
        """
        payload: dict[str, Any] = {"message": message}
        if agent:
            payload["agent"] = agent

        effective_timeout = _clamp_timeout(
            agent_timeout if agent_timeout is not None else self._default_timeout
        )
        payload["timeout"] = effective_timeout

        try:
            async with self._session.post(
                self._chat_url,
                json=payload,
                headers=self._headers,
                # Never follow redirects: they would carry the token to
                # another host.
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=effective_timeout + REQUEST_TIMEOUT_MARGIN
                ),
            ) as response:
                return await self._async_parse_response(response)
        except TimeoutError as err:
            raise PraktorTimeoutError("Praktor did not reply in time") from err
        except aiohttp.ClientError as err:
            raise PraktorConnectionError("Could not reach Praktor") from err

    async def async_validate(self) -> None:
        """Validate the configured URL and chat token.

        Praktor answers an empty message with 400 ("message is required") when
        the token is valid and with 401 when it is not.

        Raises:
            PraktorAuthError: The token is invalid.
            PraktorConnectionError: Praktor could not be reached.
            PraktorError: Praktor returned an unexpected error.
        """
        try:
            await self.async_chat("")
        except PraktorError as err:
            # Only Praktor's own answer proves the token was accepted: another
            # server that answers 400 to a JSON POST must not pass setup.
            if (
                err.status == HTTPStatus.BAD_REQUEST
                and err.message == "message is required"
            ):
                return
            raise
        raise PraktorError("Praktor accepted an empty message, which it never does")

    async def _async_parse_response(
        self, response: aiohttp.ClientResponse
    ) -> PraktorResponse:
        """Turn an HTTP response into a reply or an exception."""
        status = response.status

        if status == HTTPStatus.OK:
            data = await self._async_json(response)
            if not isinstance(data, dict):
                raise PraktorError(
                    "Praktor returned an unexpected response", status=status
                )
            reply = data.get("reply")
            if not isinstance(reply, str):
                raise PraktorError(
                    "Praktor's response did not contain a reply", status=status
                )
            agent = data.get("agent")
            return PraktorResponse(
                agent=agent if isinstance(agent, str) else "",
                reply=reply,
            )

        error_text = await self._async_error_text(response)
        _LOGGER.debug("Praktor answered HTTP %s for a chat request", status)

        if status == HTTPStatus.UNAUTHORIZED:
            raise PraktorAuthError(error_text, status=status)
        if status == HTTPStatus.FORBIDDEN:
            raise PraktorForbiddenError(error_text, status=status)
        if status == HTTPStatus.GATEWAY_TIMEOUT:
            raise PraktorTimeoutError(error_text, status=status)

        raise PraktorError(error_text, status=status)

    async def _async_json(self, response: aiohttp.ClientResponse) -> Any:
        """Decode a JSON response body, or return None when it is not JSON."""
        try:
            return await response.json(content_type=None)
        except ValueError:
            return None

    async def _async_error_text(self, response: aiohttp.ClientResponse) -> str:
        """Return Praktor's error text for a failed request.

        Falls back to a generic message so that arbitrary response bodies
        (for example a redirect body) are never surfaced.
        """
        if 300 <= response.status < 400:
            return "Praktor returned a redirect, which is not allowed"

        data = await self._async_json(response)
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, str) and error:
                return error

        return f"Praktor returned HTTP {response.status}"
