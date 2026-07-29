from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import TelegramSubscriber
from argus.storage.base_repository import BaseRepository
from argus.telegram_subscriptions import TelegramSubscriberStatus


class TelegramSubscriberRepository(
        BaseRepository[TelegramSubscriber]
):
    """Persist access, subscription and delivery state per Telegram chat."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=TelegramSubscriber,
        )

    def get_by_chat_id(
            self,
            chat_id: int,
    ) -> TelegramSubscriber | None:
        statement = select(TelegramSubscriber).where(
            TelegramSubscriber.chat_id == chat_id
        )
        return self.session.scalar(statement)

    def get_subscribed(self) -> list[TelegramSubscriber]:
        statement = (
            select(TelegramSubscriber)
            .where(
                TelegramSubscriber.status
                == TelegramSubscriberStatus.APPROVED
            )
            .where(TelegramSubscriber.is_subscribed.is_(True))
            .order_by(TelegramSubscriber.id.asc())
        )
        return list(self.session.scalars(statement).all())
