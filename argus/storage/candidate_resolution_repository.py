from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.knowledge import ManualCandidateResolutionDecision
from argus.models import (
    CandidateResolutionDecision,
    CandidateResolutionEvidence,
    Entity,
    EntityCandidate,
)
from argus.storage.base_repository import BaseRepository


class CandidateResolutionDecisionRepository(
        BaseRepository[CandidateResolutionDecision]
):
    """Append human candidate-resolution revisions."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=CandidateResolutionDecision,
        )

    def record(
            self,
            *,
            candidate: EntityCandidate,
            entity_id: int | None,
            decision: ManualCandidateResolutionDecision,
    ) -> CandidateResolutionDecision:
        previous = self.get_latest(candidate.id)
        if previous is not None and previous.scope is not decision.scope:
            raise ValueError(
                "Candidate resolution scope cannot change across revisions."
            )
        row = CandidateResolutionDecision(
            seed_entity_candidate_id=candidate.id,
            revision=1 if previous is None else previous.revision + 1,
            supersedes_candidate_resolution_decision_id=(
                None if previous is None else previous.id
            ),
            status=decision.status,
            scope=decision.scope,
            entity_id=entity_id,
            reason=decision.reason.strip(),
            reviewer=decision.reviewer.strip(),
        )
        self.add(row)
        self.flush()
        return row

    def get_latest(
            self,
            candidate_id: int,
    ) -> CandidateResolutionDecision | None:
        statement = (
            select(CandidateResolutionDecision)
            .where(
                CandidateResolutionDecision.seed_entity_candidate_id
                == candidate_id
            )
            .order_by(
                CandidateResolutionDecision.revision.desc(),
                CandidateResolutionDecision.id.desc(),
            )
            .limit(1)
        )
        return self.session.scalar(statement)


class CandidateResolutionEvidenceRepository(
        BaseRepository[CandidateResolutionEvidence]
):
    """Record exact assigned revisions applied to an entity."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=CandidateResolutionEvidence,
        )

    def record(
            self,
            *,
            entity: Entity,
            decision: CandidateResolutionDecision,
    ) -> CandidateResolutionEvidence:
        existing = self.session.scalar(
            select(CandidateResolutionEvidence).where(
                CandidateResolutionEvidence
                .candidate_resolution_decision_id
                == decision.id
            )
        )
        if existing is not None:
            if existing.entity_id != entity.id:
                raise ValueError(
                    "Candidate decision already supports another entity."
                )
            return existing
        row = CandidateResolutionEvidence(
            entity_id=entity.id,
            candidate_resolution_decision_id=decision.id,
        )
        self.add(row)
        self.flush()
        return row
