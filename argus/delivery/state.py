import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class DeliveryCursorStore(Protocol):
    """Persist the highest article identifier delivered successfully."""

    def load(self) -> int | None:
        """Return the saved cursor, or None before initialization."""

    def save(self, article_id: int) -> None:
        """Atomically persist a non-negative article identifier."""


@dataclass(frozen=True, slots=True)
class JsonDeliveryCursorStore:
    """Small durable cursor store for the single-recipient Telegram bot."""

    path: Path

    def load(self) -> int | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Invalid Telegram delivery state: {self.path}"
            ) from error
        if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or not isinstance(payload.get("last_article_id"), int)
                or isinstance(payload.get("last_article_id"), bool)
                or payload["last_article_id"] < 0
        ):
            raise ValueError(
                f"Invalid Telegram delivery state: {self.path}"
            )
        return payload["last_article_id"]

    def save(self, article_id: int) -> None:
        if article_id < 0:
            raise ValueError("article_id must not be negative.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(
            f"{self.path.suffix}.tmp"
        )
        payload = {
            "version": 1,
            "last_article_id": article_id,
        }
        temporary_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)
