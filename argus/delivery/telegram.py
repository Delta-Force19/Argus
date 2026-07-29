from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_CHARS = 4096


class TelegramAPIError(RuntimeError):
    """Describe a Telegram API failure without exposing credentials."""


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    """Minimal inbound message data needed by the Argus bot."""

    chat_id: int
    text: str


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    """Normalized Telegram update used by the application service."""

    update_id: int
    message: TelegramMessage | None


class TelegramBotClient:
    """Small synchronous adapter around the Telegram Bot HTTP API."""

    def __init__(
            self,
            *,
            token: str,
            http_client: httpx.Client | None = None,
            api_base_url: str = TELEGRAM_API_BASE_URL,
    ) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise ValueError("Telegram bot token must not be empty.")

        self._base_url = (
            f"{api_base_url.rstrip('/')}/bot{normalized_token}"
        )
        self._http_client = http_client or httpx.Client()
        self._owns_http_client = http_client is None

    def close(self) -> None:
        """Close the internally owned HTTP client."""

        if self._owns_http_client:
            self._http_client.close()

    def get_updates(
            self,
            *,
            offset: int | None,
            timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]:
        """Long-poll Telegram and normalize only required update fields."""

        parameters: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            parameters["offset"] = offset

        payload = self._request(
            "getUpdates",
            json=parameters,
            timeout=timeout_seconds + 10,
        )
        result = payload.get("result")
        if not isinstance(result, Sequence) or isinstance(
                result,
                (str, bytes),
        ):
            raise TelegramAPIError(
                "Telegram API returned an invalid update list."
            )

        updates: list[TelegramUpdate] = []
        for raw_update in result:
            normalized = _normalize_update(raw_update)
            if normalized is not None:
                updates.append(normalized)
        return tuple(updates)

    def send_message(
            self,
            *,
            chat_id: int,
            text: str,
    ) -> None:
        """Send one HTML-formatted message without link previews."""

        if len(text) > TELEGRAM_MAX_MESSAGE_CHARS:
            raise ValueError(
                "Telegram message exceeds the 4096-character limit."
            )
        self._request(
            "sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

    def _request(
            self,
            method: str,
            *,
            json: Mapping[str, Any],
            timeout: int,
    ) -> Mapping[str, Any]:
        try:
            response = self._http_client.post(
                f"{self._base_url}/{method}",
                json=dict(json),
                timeout=timeout,
            )
        except httpx.HTTPError as error:
            raise TelegramAPIError(
                f"Telegram API request failed: {type(error).__name__}."
            ) from None

        if response.status_code >= 400:
            raise TelegramAPIError(
                "Telegram API returned HTTP "
                f"{response.status_code} for {method}."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise TelegramAPIError(
                "Telegram API returned invalid JSON."
            ) from error
        if not isinstance(payload, Mapping):
            raise TelegramAPIError(
                "Telegram API returned an invalid response object."
            )
        if payload.get("ok") is not True:
            raise TelegramAPIError(
                f"Telegram API rejected {method}."
            )
        return payload


def _normalize_update(raw_update: object) -> TelegramUpdate | None:
    if not isinstance(raw_update, Mapping):
        return None
    update_id = raw_update.get("update_id")
    if not isinstance(update_id, int):
        return None

    message = raw_update.get("message")
    if not isinstance(message, Mapping):
        return TelegramUpdate(update_id=update_id, message=None)
    chat = message.get("chat")
    text = message.get("text")
    if (
            not isinstance(chat, Mapping)
            or not isinstance(chat.get("id"), int)
            or not isinstance(text, str)
    ):
        return TelegramUpdate(update_id=update_id, message=None)
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            chat_id=chat["id"],
            text=text,
        ),
    )
