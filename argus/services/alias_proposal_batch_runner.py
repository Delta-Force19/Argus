from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from argus.knowledge import EntityAliasProposer
from argus.models import DerivedArtifact
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
)


class AliasProposalBatchItemStatus(str, Enum):
    """Final state of one independently processed candidate artifact."""

    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AliasProposalBatchItemResult:
    """Detached outcome retained after the item's session is closed."""

    candidate_artifact_id: int
    status: AliasProposalBatchItemStatus
    proposal_artifact_id: int | None = None
    proposal_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AliasProposalBatchReport:
    """Aggregate and per-item results for one alias-proposal batch."""

    items: tuple[AliasProposalBatchItemResult, ...]

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def processed_count(self) -> int:
        return self._count(AliasProposalBatchItemStatus.PROCESSED)

    @property
    def failed_count(self) -> int:
        return self._count(AliasProposalBatchItemStatus.FAILED)

    @property
    def proposal_count(self) -> int:
        return sum(item.proposal_count for item in self.items)

    def _count(self, status: AliasProposalBatchItemStatus) -> int:
        return sum(item.status is status for item in self.items)


class AliasProposalBatchRunner:
    """Generate proposals with one transaction per candidate artifact."""

    def __init__(
            self,
            session_factory: Callable[[], Session],
            *,
            proposer: EntityAliasProposer,
    ) -> None:
        self._session_factory = session_factory
        self._proposer = proposer

    def run(
            self,
            candidate_artifact_ids: Iterable[int],
    ) -> AliasProposalBatchReport:
        results = tuple(
            self._run_item(candidate_artifact_id)
            for candidate_artifact_id in candidate_artifact_ids
        )
        return AliasProposalBatchReport(items=results)

    def _run_item(
            self,
            candidate_artifact_id: int,
    ) -> AliasProposalBatchItemResult:
        session = self._session_factory()

        try:
            artifact = session.get(DerivedArtifact, candidate_artifact_id)
            if artifact is None:
                raise LookupError(
                    f"Candidate artifact {candidate_artifact_id} "
                    "does not exist."
                )

            generation = AliasProposalGenerationService(
                session,
                proposer=self._proposer,
            ).generate(artifact)
            result = AliasProposalBatchItemResult(
                candidate_artifact_id=candidate_artifact_id,
                status=AliasProposalBatchItemStatus.PROCESSED,
                proposal_artifact_id=generation.artifact.id,
                proposal_count=len(generation.proposals),
            )
            session.commit()
            return result
        except Exception as error:
            session.rollback()
            return AliasProposalBatchItemResult(
                candidate_artifact_id=candidate_artifact_id,
                status=AliasProposalBatchItemStatus.FAILED,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        finally:
            session.close()
