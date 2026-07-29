from enum import Enum


class TelegramSubscriberStatus(str, Enum):
    """Administrative access state for one Telegram chat."""

    PENDING = "pending"
    APPROVED = "approved"
