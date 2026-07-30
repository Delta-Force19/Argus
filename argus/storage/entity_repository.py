from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import (
    AliasDecision,
    CandidateResolutionDecision,
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityResolutionEvidence,
)
from argus.storage.base_repository import BaseRepository


class EntityRepository(BaseRepository[Entity]):
    """Persist identities without inferring names or merge decisions."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=Entity)

    def create(
            self,
            *,
            canonical_candidate: EntityCandidate,
            creation_decision: AliasDecision,
    ) -> Entity:
        row = Entity(
            entity_type=canonical_candidate.entity_type,
            canonical_name=canonical_candidate.canonical_text,
            canonical_entity_candidate_id=canonical_candidate.id,
            created_from_alias_decision_id=creation_decision.id,
        )
        self.add(row)
        self.flush()
        return row

    def create_from_candidate_resolution(
            self,
            *,
            canonical_candidate: EntityCandidate,
            creation_decision: CandidateResolutionDecision,
    ) -> Entity:
        row = Entity(
            entity_type=canonical_candidate.entity_type,
            canonical_name=canonical_candidate.canonical_text,
            canonical_entity_candidate_id=canonical_candidate.id,
            created_from_alias_decision_id=None,
            created_from_candidate_resolution_decision_id=(
                creation_decision.id
            ),
        )
        self.add(row)
        self.flush()
        return row


class EntityCandidateAssignmentRepository(
        BaseRepository[EntityCandidateAssignment]
):
    """Assign each candidate observation to at most one identity."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=EntityCandidateAssignment,
        )

    def get_for_candidates(
            self,
            candidate_ids: Sequence[int],
    ) -> dict[int, EntityCandidateAssignment]:
        if not candidate_ids:
            return {}
        statement = select(EntityCandidateAssignment).where(
            EntityCandidateAssignment.entity_candidate_id.in_(
                candidate_ids
            )
        )
        return {
            row.entity_candidate_id: row
            for row in self.session.scalars(statement)
        }

    def assign(
            self,
            *,
            entity: Entity,
            candidate: EntityCandidate,
            decision: AliasDecision,
    ) -> EntityCandidateAssignment:
        existing = self.get_for_candidates([candidate.id]).get(
            candidate.id
        )
        if existing is not None:
            if existing.entity_id != entity.id:
                raise ValueError(
                    "Entity candidate is already assigned to another entity."
                )
            return existing

        row = EntityCandidateAssignment(
            entity_id=entity.id,
            entity_candidate_id=candidate.id,
            assigned_by_alias_decision_id=decision.id,
            assigned_by_candidate_resolution_decision_id=None,
        )
        self.add(row)
        self.flush()
        return row

    def assign_from_candidate_resolution(
            self,
            *,
            entity: Entity,
            candidate: EntityCandidate,
            decision: CandidateResolutionDecision,
    ) -> EntityCandidateAssignment:
        existing = self.get_for_candidates([candidate.id]).get(
            candidate.id
        )
        if existing is not None:
            if existing.entity_id != entity.id:
                raise ValueError(
                    "Entity candidate is already assigned to another entity."
                )
            return existing

        row = EntityCandidateAssignment(
            entity_id=entity.id,
            entity_candidate_id=candidate.id,
            assigned_by_alias_decision_id=None,
            assigned_by_candidate_resolution_decision_id=decision.id,
        )
        self.add(row)
        self.flush()
        return row


class EntityResolutionEvidenceRepository(
        BaseRepository[EntityResolutionEvidence]
):
    """Link an identity to each exact approval used to resolve it."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=EntityResolutionEvidence,
        )

    def get_by_decision(
            self,
            decision_id: int,
    ) -> EntityResolutionEvidence | None:
        statement = select(EntityResolutionEvidence).where(
            EntityResolutionEvidence.alias_decision_id == decision_id
        )
        return self.session.scalar(statement)

    def record(
            self,
            *,
            entity: Entity,
            decision: AliasDecision,
    ) -> EntityResolutionEvidence:
        existing = self.get_by_decision(decision.id)
        if existing is not None:
            if existing.entity_id != entity.id:
                raise ValueError(
                    "Alias decision already supports another entity."
                )
            return existing

        row = EntityResolutionEvidence(
            entity_id=entity.id,
            alias_decision_id=decision.id,
        )
        self.add(row)
        self.flush()
        return row
