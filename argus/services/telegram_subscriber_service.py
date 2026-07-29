from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.models import TelegramSubscriber
from argus.storage.telegram_subscriber_repository import (
    TelegramSubscriberRepository,
)
from argus.telegram_subscriptions import TelegramSubscriberStatus


@dataclass(frozen=True, slots=True)
class TelegramSubscriberView:
    """Detached subscriber state safe to use after a session closes."""

    chat_id: int
    status: TelegramSubscriberStatus
    is_subscribed: bool
    last_delivered_article_id: int | None

    @property
    def is_approved(self) -> bool:
        return self.status is TelegramSubscriberStatus.APPROVED


class TelegramSubscriberService:
    """Own transactional changes to Telegram access and delivery state."""

    def __init__(
            self,
            *,
            session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def bootstrap_admin(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> TelegramSubscriberView:
        """Create the configured administrator without overriding choices."""

        _validate_cursor(initial_cursor)
        with self._session_factory() as session:
            repository = TelegramSubscriberRepository(session)
            subscriber = repository.get_by_chat_id(chat_id)
            if subscriber is None:
                subscriber = TelegramSubscriber(
                    chat_id=chat_id,
                    status=TelegramSubscriberStatus.APPROVED,
                    is_subscribed=True,
                    last_delivered_article_id=initial_cursor,
                )
                repository.add(subscriber)
            else:
                was_approved = (
                    subscriber.status
                    is TelegramSubscriberStatus.APPROVED
                )
                subscriber.status = TelegramSubscriberStatus.APPROVED
                if not was_approved:
                    subscriber.is_subscribed = True
                    subscriber.last_delivered_article_id = initial_cursor
                elif subscriber.last_delivered_article_id is None:
                    subscriber.last_delivered_article_id = initial_cursor
            session.commit()
            return _to_view(subscriber)

    def request_access(
            self,
            *,
            chat_id: int,
    ) -> tuple[TelegramSubscriberView, bool]:
        """Create a pending request and report whether it is new."""

        with self._session_factory() as session:
            repository = TelegramSubscriberRepository(session)
            subscriber = repository.get_by_chat_id(chat_id)
            created = subscriber is None
            if subscriber is None:
                subscriber = TelegramSubscriber(
                    chat_id=chat_id,
                    status=TelegramSubscriberStatus.PENDING,
                    is_subscribed=False,
                )
                repository.add(subscriber)
                session.commit()
            return _to_view(subscriber), created

    def register(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> tuple[TelegramSubscriberView, bool]:
        """Activate a public user without enabling automatic delivery."""

        _validate_cursor(initial_cursor)
        with self._session_factory() as session:
            repository = TelegramSubscriberRepository(session)
            subscriber = repository.get_by_chat_id(chat_id)
            created = subscriber is None
            if subscriber is None:
                subscriber = TelegramSubscriber(
                    chat_id=chat_id,
                    status=TelegramSubscriberStatus.APPROVED,
                    is_subscribed=False,
                    last_delivered_article_id=initial_cursor,
                )
                repository.add(subscriber)
            elif (
                    subscriber.status
                    is not TelegramSubscriberStatus.APPROVED
            ):
                subscriber.status = TelegramSubscriberStatus.APPROVED
                subscriber.is_subscribed = False
                subscriber.last_delivered_article_id = initial_cursor
            session.commit()
            return _to_view(subscriber), created

    def get(
            self,
            *,
            chat_id: int,
    ) -> TelegramSubscriberView | None:
        with self._session_factory() as session:
            subscriber = TelegramSubscriberRepository(
                session
            ).get_by_chat_id(chat_id)
            return None if subscriber is None else _to_view(subscriber)

    def approve(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> TelegramSubscriberView | None:
        """Approve an existing request without enabling delivery."""

        _validate_cursor(initial_cursor)
        with self._session_factory() as session:
            subscriber = TelegramSubscriberRepository(
                session
            ).get_by_chat_id(chat_id)
            if subscriber is None:
                return None
            subscriber.status = TelegramSubscriberStatus.APPROVED
            subscriber.is_subscribed = False
            subscriber.last_delivered_article_id = initial_cursor
            session.commit()
            return _to_view(subscriber)

    def subscribe(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> TelegramSubscriberView | None:
        """Enable future delivery from the current ingestion boundary."""

        _validate_cursor(initial_cursor)
        with self._session_factory() as session:
            subscriber = TelegramSubscriberRepository(
                session
            ).get_by_chat_id(chat_id)
            if (
                    subscriber is None
                    or subscriber.status
                    is not TelegramSubscriberStatus.APPROVED
            ):
                return None
            if not subscriber.is_subscribed:
                subscriber.is_subscribed = True
                subscriber.last_delivered_article_id = initial_cursor
            session.commit()
            return _to_view(subscriber)

    def unsubscribe(
            self,
            *,
            chat_id: int,
    ) -> TelegramSubscriberView | None:
        with self._session_factory() as session:
            subscriber = TelegramSubscriberRepository(
                session
            ).get_by_chat_id(chat_id)
            if (
                    subscriber is None
                    or subscriber.status
                    is not TelegramSubscriberStatus.APPROVED
            ):
                return None
            subscriber.is_subscribed = False
            session.commit()
            return _to_view(subscriber)

    def forget(
            self,
            *,
            chat_id: int,
    ) -> bool:
        """Delete all locally persisted state for one Telegram chat."""

        with self._session_factory() as session:
            repository = TelegramSubscriberRepository(session)
            subscriber = repository.get_by_chat_id(chat_id)
            if subscriber is None:
                return False
            repository.delete(subscriber)
            session.commit()
            return True

    def get_subscribed(self) -> tuple[TelegramSubscriberView, ...]:
        with self._session_factory() as session:
            subscribers = TelegramSubscriberRepository(
                session
            ).get_subscribed()
            return tuple(_to_view(item) for item in subscribers)

    def advance_cursor(
            self,
            *,
            chat_id: int,
            article_id: int,
    ) -> None:
        """Advance one recipient only after Telegram accepts its batch."""

        _validate_cursor(article_id)
        with self._session_factory() as session:
            subscriber = TelegramSubscriberRepository(
                session
            ).get_by_chat_id(chat_id)
            if subscriber is None:
                raise ValueError(
                    f"Unknown Telegram subscriber: {chat_id}."
                )
            current = subscriber.last_delivered_article_id
            if current is not None and article_id < current:
                raise ValueError(
                    "Telegram delivery cursor cannot move backwards."
                )
            subscriber.last_delivered_article_id = article_id
            session.commit()


def _to_view(
        subscriber: TelegramSubscriber,
) -> TelegramSubscriberView:
    return TelegramSubscriberView(
        chat_id=subscriber.chat_id,
        status=subscriber.status,
        is_subscribed=subscriber.is_subscribed,
        last_delivered_article_id=(
            subscriber.last_delivered_article_id
        ),
    )


def _validate_cursor(article_id: int) -> None:
    if article_id < 0:
        raise ValueError("article_id must not be negative.")
