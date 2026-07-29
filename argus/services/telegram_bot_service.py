from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import logging
from math import ceil
import os
from pathlib import Path
import time
from typing import Protocol
from zoneinfo import ZoneInfo

from argus.delivery.state import JsonDeliveryCursorStore
from argus.delivery.telegram import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    TelegramBotClient,
    TelegramUpdate,
)
from argus.services.latest_news_service import (
    LatestNewsItem,
    LatestNewsReport,
    get_highest_article_id,
    get_latest_news,
    get_news_after_article_id,
)
from argus.services.collection_service import collect_articles
from argus.services.parsing_service import parse_articles
from argus.services.telegram_subscriber_service import (
    TelegramSubscriberService,
)


LOGGER = logging.getLogger(__name__)
BOT_TOKEN_ENVIRONMENT_VARIABLE = "ARGUS_TELEGRAM_BOT_TOKEN"
ALLOWED_CHAT_ID_ENVIRONMENT_VARIABLE = (
    "ARGUS_TELEGRAM_ALLOWED_CHAT_ID"
)
ADMIN_CHAT_ID_ENVIRONMENT_VARIABLE = "ARGUS_TELEGRAM_ADMIN_CHAT_ID"
TELEGRAM_MESSAGE_CONTENT_LIMIT = 3900
DEFAULT_DELIVERY_STATE_PATH = Path(
    "data/telegram_delivery_state.json"
)
DEFAULT_LATEST_COOLDOWN_SECONDS = 10


class TelegramGateway(Protocol):
    """Application-facing Telegram operations."""

    def get_updates(
            self,
            *,
            offset: int | None,
            timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]:
        """Return normalized updates at or after the supplied offset."""

    def send_message(
            self,
            *,
            chat_id: int,
            text: str,
    ) -> None:
        """Send one message."""


@dataclass(frozen=True, slots=True)
class TelegramBotSettings:
    """Runtime-only credentials and authorization settings."""

    bot_token: str
    admin_chat_id: int | None

    @classmethod
    def from_environment(
            cls,
            environ: Mapping[str, str] = os.environ,
    ) -> "TelegramBotSettings":
        """Load secrets without accepting them as CLI arguments."""

        token = environ.get(BOT_TOKEN_ENVIRONMENT_VARIABLE, "").strip()
        if not token:
            raise ValueError(
                f"{BOT_TOKEN_ENVIRONMENT_VARIABLE} is required."
            )

        raw_chat_id = (
            environ.get(
                ADMIN_CHAT_ID_ENVIRONMENT_VARIABLE,
                "",
            ).strip()
            or environ.get(
                ALLOWED_CHAT_ID_ENVIRONMENT_VARIABLE,
                "",
            ).strip()
        )
        if not raw_chat_id:
            return cls(bot_token=token, admin_chat_id=None)
        try:
            admin_chat_id = int(raw_chat_id)
        except ValueError as error:
            raise ValueError(
                f"{ADMIN_CHAT_ID_ENVIRONMENT_VARIABLE} "
                "must be an integer."
            ) from error

        return cls(
            bot_token=token,
            admin_chat_id=admin_chat_id,
        )


class TelegramNewsBot:
    """Authorize Telegram commands and deliver Argus reader output."""

    def __init__(
            self,
            *,
            gateway: TelegramGateway,
            subscribers: TelegramSubscriberService,
            output_timezone: ZoneInfo,
            news_limit: int = 10,
            excerpt_chars: int = 500,
            news_loader: Callable[..., LatestNewsReport] = get_latest_news,
            highest_article_id_loader: Callable[[], int] = (
                get_highest_article_id
            ),
            automatic_delivery: "TelegramAutomaticDelivery | None" = None,
            automatic_interval_seconds: int = 3600,
            latest_cooldown_seconds: int = (
                DEFAULT_LATEST_COOLDOWN_SECONDS
            ),
            monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if news_limit < 1:
            raise ValueError("news_limit must be greater than zero.")
        if excerpt_chars < 40:
            raise ValueError("excerpt_chars must be at least 40.")
        if automatic_interval_seconds < 1:
            raise ValueError(
                "automatic_interval_seconds must be greater than zero."
            )
        if latest_cooldown_seconds < 0:
            raise ValueError(
                "latest_cooldown_seconds must not be negative."
            )

        self._gateway = gateway
        self._subscribers = subscribers
        self._output_timezone = output_timezone
        self._news_limit = news_limit
        self._excerpt_chars = excerpt_chars
        self._news_loader = news_loader
        self._highest_article_id_loader = highest_article_id_loader
        self._automatic_delivery = automatic_delivery
        self._automatic_interval_seconds = automatic_interval_seconds
        self._latest_cooldown_seconds = latest_cooldown_seconds
        self._monotonic = monotonic
        self._latest_request_times: dict[int, float] = {}

    def poll(
            self,
            *,
            timeout_seconds: int = 30,
            max_polls: int | None = None,
    ) -> None:
        """Long-poll until interrupted or an optional test bound is met."""

        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be greater than zero.")
        if max_polls is not None and max_polls < 1:
            raise ValueError("max_polls must be greater than zero.")

        offset: int | None = None
        poll_count = 0
        next_automatic_cycle_at = self._monotonic()
        while max_polls is None or poll_count < max_polls:
            if (
                    self._automatic_delivery is not None
                    and self._monotonic() >= next_automatic_cycle_at
            ):
                try:
                    self._automatic_delivery.run_cycle()
                except Exception:
                    LOGGER.exception(
                        "Telegram automatic delivery cycle failed."
                    )
                next_automatic_cycle_at = (
                    self._monotonic()
                    + self._automatic_interval_seconds
                )
            updates = self._gateway.get_updates(
                offset=offset,
                timeout_seconds=timeout_seconds,
            )
            poll_count += 1
            for update in updates:
                offset = max(
                    offset or update.update_id + 1,
                    update.update_id + 1,
                )
                self._handle_update(update)

    def _handle_update(self, update: TelegramUpdate) -> None:
        message = update.message
        if message is None:
            return
        command, _ = _parse_command(message.text)
        if command is None:
            return

        if command == "start":
            self._handle_start(message.chat_id)
            return
        if command == "forgetme":
            self._handle_forget(message.chat_id)
            return
        if command not in {"latest", "subscribe", "unsubscribe"}:
            return

        self._subscribers.register(
            chat_id=message.chat_id,
            initial_cursor=self._highest_article_id_loader(),
        )

        if command == "subscribe":
            self._subscribers.subscribe(
                chat_id=message.chat_id,
                initial_cursor=self._highest_article_id_loader(),
            )
            self._send(
                message.chat_id,
                "Automatic delivery enabled. Only future news will be sent.",
            )
            return
        if command == "unsubscribe":
            self._subscribers.unsubscribe(chat_id=message.chat_id)
            self._send(
                message.chat_id,
                "Automatic delivery disabled. /latest remains available.",
            )
            return
        if command != "latest":
            return
        if not self._accept_latest_request(message.chat_id):
            return

        report = self._news_loader(
            limit=self._news_limit,
            excerpt_chars=self._excerpt_chars,
        )
        for output_message in format_latest_news_messages(
                report,
                output_timezone=self._output_timezone,
        ):
            self._gateway.send_message(
                chat_id=message.chat_id,
                text=output_message,
            )

    def _handle_start(self, chat_id: int) -> None:
        self._subscribers.register(
            chat_id=chat_id,
            initial_cursor=self._highest_article_id_loader(),
        )
        self._send(
            chat_id,
            (
                "Access is active. Use /latest for the current feed "
                "or /subscribe for automatic delivery."
            ),
        )

    def _handle_forget(self, chat_id: int) -> None:
        self._subscribers.forget(chat_id=chat_id)
        self._latest_request_times.pop(chat_id, None)
        self._send(
            chat_id,
            (
                "Your locally stored Argus subscription and delivery "
                "state has been deleted. Telegram may retain its own "
                "message and bot interaction records."
            ),
        )

    def _accept_latest_request(self, chat_id: int) -> bool:
        now = self._monotonic()
        previous = self._latest_request_times.get(chat_id)
        if (
                previous is not None
                and now - previous < self._latest_cooldown_seconds
        ):
            remaining_seconds = ceil(
                self._latest_cooldown_seconds - (now - previous)
            )
            self._send(
                chat_id,
                (
                    "Please wait "
                    f"{remaining_seconds} seconds before using /latest again."
                ),
            )
            return False
        self._latest_request_times[chat_id] = now
        return True

    def _send(self, chat_id: int, text: str) -> None:
        self._gateway.send_message(chat_id=chat_id, text=text)


@dataclass(frozen=True, slots=True)
class TelegramOutboundBatch:
    """One successfully acknowledgeable Telegram delivery unit."""

    text: str
    last_article_id: int


class TelegramAutomaticDelivery:
    """Collect, parse and deliver only articles beyond a durable cursor."""

    def __init__(
            self,
            *,
            gateway: TelegramGateway,
            subscribers: TelegramSubscriberService,
            output_timezone: ZoneInfo,
            delivery_limit: int = 20,
            parse_limit: int = 20,
            excerpt_chars: int = 500,
            collector: Callable[[], None] = collect_articles,
            parser: Callable[..., None] = parse_articles,
            news_loader: Callable[..., LatestNewsReport] = (
                get_news_after_article_id
            ),
    ) -> None:
        if delivery_limit < 1:
            raise ValueError("delivery_limit must be greater than zero.")
        if parse_limit < 1:
            raise ValueError("parse_limit must be greater than zero.")
        if excerpt_chars < 40:
            raise ValueError("excerpt_chars must be at least 40.")
        self._gateway = gateway
        self._subscribers = subscribers
        self._output_timezone = output_timezone
        self._delivery_limit = delivery_limit
        self._parse_limit = parse_limit
        self._excerpt_chars = excerpt_chars
        self._collector = collector
        self._parser = parser
        self._news_loader = news_loader

    def run_cycle(self) -> int:
        """Run one bounded cycle and return the delivered article count."""

        subscribers = self._subscribers.get_subscribed()
        if not subscribers:
            return 0
        cursors = tuple(
            subscriber.last_delivered_article_id
            for subscriber in subscribers
        )
        if any(cursor is None for cursor in cursors):
            raise ValueError(
                "Subscribed Telegram user has no delivery cursor."
            )
        earliest_cursor = min(
            cursor for cursor in cursors if cursor is not None
        )
        self._collector()
        self._parser(
            limit=self._parse_limit,
            retry_failed=False,
            newest_first=False,
            after_article_id=earliest_cursor,
        )

        delivered_count = 0
        for subscriber in subscribers:
            cursor = subscriber.last_delivered_article_id
            if cursor is None:
                continue
            report = self._news_loader(
                after_article_id=cursor,
                limit=self._delivery_limit,
                excerpt_chars=self._excerpt_chars,
            )
            batches = format_automatic_news_batches(
                report,
                output_timezone=self._output_timezone,
            )
            try:
                for batch in batches:
                    self._gateway.send_message(
                        chat_id=subscriber.chat_id,
                        text=batch.text,
                    )
                    previous_cursor = cursor
                    cursor = batch.last_article_id
                    self._subscribers.advance_cursor(
                        chat_id=subscriber.chat_id,
                        article_id=cursor,
                    )
                    delivered_count += sum(
                        previous_cursor < item.article_id <= cursor
                        for item in report.items
                    )
            except Exception:
                LOGGER.exception(
                    "Telegram delivery failed for one recipient.",
                )
        return delivered_count


def run_telegram_news_bot(
        *,
        news_limit: int = 10,
        excerpt_chars: int = 500,
        output_timezone: ZoneInfo,
        poll_timeout_seconds: int = 30,
        run_once: bool = False,
        automatic_delivery: bool = False,
        automatic_interval_seconds: int = 3600,
        automatic_delivery_limit: int = 20,
        automatic_parse_limit: int = 20,
        latest_cooldown_seconds: int = (
            DEFAULT_LATEST_COOLDOWN_SECONDS
        ),
        delivery_state_path: Path = DEFAULT_DELIVERY_STATE_PATH,
) -> None:
    """Build the production adapter and run manual long polling."""

    settings = TelegramBotSettings.from_environment()
    client = TelegramBotClient(token=settings.bot_token)
    try:
        subscribers = TelegramSubscriberService()
        if settings.admin_chat_id is not None:
            existing_admin = subscribers.get(
                chat_id=settings.admin_chat_id
            )
            legacy_cursor = None
            if existing_admin is None:
                legacy_cursor = JsonDeliveryCursorStore(
                    delivery_state_path
                ).load()
            subscribers.bootstrap_admin(
                chat_id=settings.admin_chat_id,
                initial_cursor=(
                    legacy_cursor
                    if legacy_cursor is not None
                    else get_highest_article_id()
                ),
            )
        delivery = None
        if automatic_delivery:
            if run_once:
                raise ValueError(
                    "--once cannot be combined with --auto-delivery."
                )
            delivery = TelegramAutomaticDelivery(
                gateway=client,
                subscribers=subscribers,
                output_timezone=output_timezone,
                delivery_limit=automatic_delivery_limit,
                parse_limit=automatic_parse_limit,
                excerpt_chars=excerpt_chars,
            )
        bot = TelegramNewsBot(
            gateway=client,
            subscribers=subscribers,
            output_timezone=output_timezone,
            news_limit=news_limit,
            excerpt_chars=excerpt_chars,
            automatic_delivery=delivery,
            automatic_interval_seconds=automatic_interval_seconds,
            latest_cooldown_seconds=latest_cooldown_seconds,
        )
        bot.poll(
            timeout_seconds=poll_timeout_seconds,
            max_polls=1 if run_once else None,
        )
    finally:
        client.close()


def format_latest_news_messages(
        report: LatestNewsReport,
        *,
        output_timezone: ZoneInfo,
) -> tuple[str, ...]:
    """Render bounded Telegram HTML messages at article boundaries."""

    header = (
        "<b>Argus — latest collected news</b>\n"
        f"Shown: {len(report.items)} · "
        f"full text: {report.content_count} · "
        f"summary: {report.summary_count} · "
        f"headline only: {report.headline_only_count}"
    )
    sections = tuple(
        _format_news_item(
            item,
            position=position,
            output_timezone=output_timezone,
        )
        for position, item in enumerate(report.items, start=1)
    )
    if not sections:
        return (f"{header}\n\nNo collected news is available.",)
    return _pack_sections(header=header, sections=sections)


def format_automatic_news_batches(
        report: LatestNewsReport,
        *,
        output_timezone: ZoneInfo,
) -> tuple[TelegramOutboundBatch, ...]:
    """Render new articles into cursor-safe Telegram messages."""

    if not report.items:
        return ()
    header = "<b>Argus — new collected news</b>"
    sections = tuple(
        (
            _format_news_item(
                item,
                position=position,
                output_timezone=output_timezone,
            ),
            item.article_id,
        )
        for position, item in enumerate(report.items, start=1)
    )
    return _pack_cursor_sections(header=header, sections=sections)


def _format_news_item(
        item: LatestNewsItem,
        *,
        position: int,
        output_timezone: ZoneInfo,
) -> str:
    published_at = _format_datetime(
        item.published_at,
        output_timezone,
    )
    lines = [
        f"<b>{position}. {escape(_truncate(item.title, 500))}</b>",
        (
            f"{escape(_truncate(item.source, 200))} · "
            f"{escape(published_at)}"
        ),
    ]
    if item.excerpt is not None:
        lines.append(escape(_truncate(item.excerpt, 2000)))
    lines.append(
        '<a href="'
        f'{escape(_truncate(item.url, 2048), quote=True)}'
        '">Open source</a>'
    )
    return "\n".join(lines)


def _format_datetime(
        value: datetime | None,
        output_timezone: ZoneInfo,
) -> str:
    if value is None:
        return "time unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(output_timezone).strftime(
        "%Y-%m-%d %H:%M %Z"
    )


def _pack_sections(
        *,
        header: str,
        sections: Sequence[str],
) -> tuple[str, ...]:
    messages: list[str] = []
    current = header
    for section in sections:
        candidate = f"{current}\n\n{section}"
        if len(candidate) <= TELEGRAM_MESSAGE_CONTENT_LIMIT:
            current = candidate
            continue
        messages.append(current)
        current = f"{header}\n\n{section}"
        if len(current) > TELEGRAM_MESSAGE_CONTENT_LIMIT:
            raise ValueError(
                "One rendered news item exceeds the Telegram limit."
            )
    messages.append(current)
    if any(
            len(message) > TELEGRAM_MAX_MESSAGE_CHARS
            for message in messages
    ):
        raise ValueError("Rendered Telegram message is too long.")
    return tuple(messages)


def _pack_cursor_sections(
        *,
        header: str,
        sections: Sequence[tuple[str, int]],
) -> tuple[TelegramOutboundBatch, ...]:
    messages: list[TelegramOutboundBatch] = []
    current = header
    current_last_article_id = 0
    for section, article_id in sections:
        candidate = f"{current}\n\n{section}"
        if len(candidate) <= TELEGRAM_MESSAGE_CONTENT_LIMIT:
            current = candidate
            current_last_article_id = article_id
            continue
        if current_last_article_id == 0:
            raise ValueError(
                "One rendered news item exceeds the Telegram limit."
            )
        messages.append(
            TelegramOutboundBatch(
                text=current,
                last_article_id=current_last_article_id,
            )
        )
        current = f"{header}\n\n{section}"
        current_last_article_id = article_id
        if len(current) > TELEGRAM_MESSAGE_CONTENT_LIMIT:
            raise ValueError(
                "One rendered news item exceeds the Telegram limit."
            )
    messages.append(
        TelegramOutboundBatch(
            text=current,
            last_article_id=current_last_article_id,
        )
    )
    return tuple(messages)


def _parse_command(text: str) -> tuple[str | None, str | None]:
    parts = text.strip().split(maxsplit=1)
    first_token = parts[0] if parts else ""
    if not first_token.startswith("/"):
        return None, None
    command = first_token[1:].split("@", maxsplit=1)[0].casefold()
    argument = parts[1].strip() if len(parts) == 2 else None
    return command, argument


def _command_name(text: str) -> str | None:
    """Retain the original helper for callers that only need the name."""

    return _parse_command(text)[0]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit - 1].rstrip()}…"
