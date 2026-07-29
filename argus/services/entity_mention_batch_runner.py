from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from argus.knowledge import EntityRecognizer
from argus.models import DerivedArtifact
from argus.services.entity_mention_extraction_service import (
    EntityMentionExtractionService,
)


class EntityMentionBatchItemStatus(str, Enum):
    """Final state of one independently executed text artifact."""

    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EntityMentionBatchItemResult:
    """Detached outcome retained after the item's session is closed."""

    text_artifact_id: int
    status: EntityMentionBatchItemStatus
    entity_artifact_id: int | None = None
    mention_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class EntityMentionBatchReport:
    """Aggregate and per-item results for one mention-extraction batch."""

    items: tuple[EntityMentionBatchItemResult, ...]

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def processed_count(self) -> int:
        return self._count(EntityMentionBatchItemStatus.PROCESSED)

    @property
    def failed_count(self) -> int:
        return self._count(EntityMentionBatchItemStatus.FAILED)

    @property
    def mention_count(self) -> int:
        return sum(item.mention_count for item in self.items)

    def _count(self, status: EntityMentionBatchItemStatus) -> int:
        return sum(item.status is status for item in self.items)


class EntityMentionBatchRunner:
    """Extract mentions with one isolated transaction per text artifact."""

    def __init__(
            self,
            session_factory: Callable[[], Session],
            *,
            recognizer: EntityRecognizer,
    ) -> None:
        self._session_factory = session_factory
        self._recognizer = recognizer

    def run(
            self,
            text_artifact_ids: Iterable[int],
    ) -> EntityMentionBatchReport:
        results = tuple(
            self._run_item(text_artifact_id)
            for text_artifact_id in text_artifact_ids
        )
        return EntityMentionBatchReport(items=results)

    def _run_item(
            self,
            text_artifact_id: int,
    ) -> EntityMentionBatchItemResult:
        session = self._session_factory()

        try:
            artifact = session.get(DerivedArtifact, text_artifact_id)
            if artifact is None:
                raise LookupError(
                    f"Text artifact {text_artifact_id} does not exist."
                )

            extraction = EntityMentionExtractionService(
                session,
                recognizer=self._recognizer,
            ).extract(artifact)
            result = EntityMentionBatchItemResult(
                text_artifact_id=text_artifact_id,
                status=EntityMentionBatchItemStatus.PROCESSED,
                entity_artifact_id=extraction.artifact.id,
                mention_count=len(extraction.mentions),
            )
            session.commit()
            return result
        except Exception as error:
            session.rollback()
            return EntityMentionBatchItemResult(
                text_artifact_id=text_artifact_id,
                status=EntityMentionBatchItemStatus.FAILED,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        finally:
            session.close()
