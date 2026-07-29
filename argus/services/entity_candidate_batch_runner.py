from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from argus.knowledge import EntityCandidateCanonicalizer
from argus.models import DerivedArtifact
from argus.services.entity_candidate_generation_service import (
    EntityCandidateGenerationService,
)


class EntityCandidateBatchItemStatus(str, Enum):
    """Final state of one independently processed mention artifact."""

    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EntityCandidateBatchItemResult:
    """Detached outcome retained after the item's session is closed."""

    mention_artifact_id: int
    status: EntityCandidateBatchItemStatus
    candidate_artifact_id: int | None = None
    candidate_count: int = 0
    excluded_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class EntityCandidateBatchReport:
    """Aggregate and per-item results for one candidate-generation batch."""

    items: tuple[EntityCandidateBatchItemResult, ...]

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def processed_count(self) -> int:
        return self._count(EntityCandidateBatchItemStatus.PROCESSED)

    @property
    def failed_count(self) -> int:
        return self._count(EntityCandidateBatchItemStatus.FAILED)

    @property
    def candidate_count(self) -> int:
        return sum(item.candidate_count for item in self.items)

    @property
    def excluded_count(self) -> int:
        return sum(item.excluded_count for item in self.items)

    def _count(self, status: EntityCandidateBatchItemStatus) -> int:
        return sum(item.status is status for item in self.items)


class EntityCandidateBatchRunner:
    """Generate candidates with one transaction per mention artifact."""

    def __init__(
            self,
            session_factory: Callable[[], Session],
            *,
            canonicalizer: EntityCandidateCanonicalizer,
    ) -> None:
        self._session_factory = session_factory
        self._canonicalizer = canonicalizer

    def run(
            self,
            mention_artifact_ids: Iterable[int],
    ) -> EntityCandidateBatchReport:
        results = tuple(
            self._run_item(mention_artifact_id)
            for mention_artifact_id in mention_artifact_ids
        )
        return EntityCandidateBatchReport(items=results)

    def _run_item(
            self,
            mention_artifact_id: int,
    ) -> EntityCandidateBatchItemResult:
        session = self._session_factory()

        try:
            artifact = session.get(DerivedArtifact, mention_artifact_id)
            if artifact is None:
                raise LookupError(
                    f"Mention artifact {mention_artifact_id} does not exist."
                )

            generation = EntityCandidateGenerationService(
                session,
                canonicalizer=self._canonicalizer,
            ).generate(artifact)
            decision_count = len(
                generation.artifact.payload.get("decisions", ())
            )
            candidate_count = len(generation.candidates)
            result = EntityCandidateBatchItemResult(
                mention_artifact_id=mention_artifact_id,
                status=EntityCandidateBatchItemStatus.PROCESSED,
                candidate_artifact_id=generation.artifact.id,
                candidate_count=candidate_count,
                excluded_count=decision_count - candidate_count,
            )
            session.commit()
            return result
        except Exception as error:
            session.rollback()
            return EntityCandidateBatchItemResult(
                mention_artifact_id=mention_artifact_id,
                status=EntityCandidateBatchItemStatus.FAILED,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        finally:
            session.close()
