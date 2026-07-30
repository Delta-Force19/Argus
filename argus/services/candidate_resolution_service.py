from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import (
    CandidateResolutionScope,
    CandidateResolutionStatus,
    ManualCandidateResolutionDecision,
)
from argus.models import (
    Entity,
    EntityCandidate,
    EntityMention,
)
from argus.services.entity_candidate_provenance_service import (
    resolve_entity_candidate_provenance,
)
from argus.storage.candidate_resolution_repository import (
    CandidateResolutionDecisionRepository,
    CandidateResolutionEvidenceRepository,
)
from argus.storage.entity_repository import (
    EntityCandidateAssignmentRepository,
    EntityRepository,
)


@dataclass(frozen=True, slots=True)
class CandidateResolutionResult:
    """Detached result of one explicit candidate-resolution revision."""

    decision_id: int
    revision: int
    supersedes_decision_id: int | None
    status: CandidateResolutionStatus
    scope: CandidateResolutionScope
    seed_entity_candidate_id: int
    entity_id: int
    entity_type: str
    canonical_name: str
    entity_created: bool
    matched_candidate_ids: tuple[int, ...]
    newly_assigned_candidate_ids: tuple[int, ...]


class CandidateResolutionService:
    """Apply explicit candidate identity decisions without alias inference.

    The service never commits. The caller owns the transaction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._decisions = CandidateResolutionDecisionRepository(session)
        self._evidence = CandidateResolutionEvidenceRepository(session)
        self._entities = EntityRepository(session)
        self._assignments = EntityCandidateAssignmentRepository(session)

    def decide(
            self,
            *,
            candidate_id: int,
            decision: ManualCandidateResolutionDecision,
            entity_id: int | None = None,
    ) -> CandidateResolutionResult:
        if candidate_id < 1:
            raise ValueError("candidate_id must be positive.")
        if entity_id is not None and entity_id < 1:
            raise ValueError("entity_id must be positive.")

        candidate = self._session.get(EntityCandidate, candidate_id)
        if candidate is None:
            raise ValueError("Entity candidate does not exist.")
        previous = self._decisions.get_latest(candidate.id)

        if decision.status is CandidateResolutionStatus.REVOKED:
            return self._revoke(
                candidate=candidate,
                previous=previous,
                decision=decision,
                entity_id=entity_id,
            )
        return self._assign(
            candidate=candidate,
            previous=previous,
            decision=decision,
            entity_id=entity_id,
        )

    def _assign(
            self,
            *,
            candidate: EntityCandidate,
            previous,
            decision: ManualCandidateResolutionDecision,
            entity_id: int | None,
    ) -> CandidateResolutionResult:
        if previous is not None:
            if previous.entity_id is None:
                raise ValueError(
                    "Previous candidate resolution has no target entity."
                )
            if entity_id is not None and entity_id != previous.entity_id:
                raise ValueError(
                    "Candidate reassignment requires an explicit entity "
                    "merge or reassignment workflow."
                )
            entity_id = previous.entity_id

        candidates = self._resolve_scope(
            seed=candidate,
            scope=decision.scope,
        )
        self._validate_provenance(candidates)
        assignments = self._assignments.get_for_candidates(
            [item.id for item in candidates]
        )
        assigned_entity_ids = {
            item.entity_id for item in assignments.values()
        }
        if len(assigned_entity_ids) > 1:
            raise ValueError(
                "Candidate scope spans multiple existing entities; "
                "an explicit entity merge is required."
            )
        inferred_entity_id = next(iter(assigned_entity_ids), None)
        if (
                entity_id is not None
                and inferred_entity_id is not None
                and entity_id != inferred_entity_id
        ):
            raise ValueError(
                "Requested entity conflicts with an existing assignment."
            )
        target_entity_id = (
            entity_id if entity_id is not None else inferred_entity_id
        )

        row = self._decisions.record(
            candidate=candidate,
            entity_id=target_entity_id,
            decision=decision,
        )
        entity_created = target_entity_id is None
        if entity_created:
            entity = self._entities.create_from_candidate_resolution(
                canonical_candidate=candidate,
                creation_decision=row,
            )
            row.entity_id = entity.id
            self._session.flush()
        else:
            entity = self._session.get(Entity, target_entity_id)
            if entity is None:
                raise ValueError("Entity does not exist.")
            if entity.entity_type is not candidate.entity_type:
                raise ValueError(
                    "Entity type conflicts with the candidate."
                )

        newly_assigned: list[int] = []
        for scoped_candidate in candidates:
            if scoped_candidate.entity_type is not entity.entity_type:
                raise ValueError(
                    "Candidate scope contains conflicting entity types."
                )
            if scoped_candidate.id not in assignments:
                newly_assigned.append(scoped_candidate.id)
            self._assignments.assign_from_candidate_resolution(
                entity=entity,
                candidate=scoped_candidate,
                decision=row,
            )
        self._evidence.record(entity=entity, decision=row)

        return self._result(
            row=row,
            entity=entity,
            entity_created=entity_created,
            matched_candidates=candidates,
            newly_assigned_candidate_ids=tuple(newly_assigned),
        )

    def _revoke(
            self,
            *,
            candidate: EntityCandidate,
            previous,
            decision: ManualCandidateResolutionDecision,
            entity_id: int | None,
    ) -> CandidateResolutionResult:
        if previous is None or previous.entity_id is None:
            raise ValueError(
                "Candidate resolution cannot be revoked before assignment."
            )
        if entity_id is not None:
            raise ValueError(
                "entity_id must not be supplied when revoking."
            )
        entity = self._session.get(Entity, previous.entity_id)
        if entity is None:
            raise ValueError(
                "Previous candidate resolution references a missing entity."
            )
        candidates = self._resolve_scope(
            seed=candidate,
            scope=decision.scope,
        )
        row = self._decisions.record(
            candidate=candidate,
            entity_id=entity.id,
            decision=decision,
        )
        return self._result(
            row=row,
            entity=entity,
            entity_created=False,
            matched_candidates=candidates,
            newly_assigned_candidate_ids=(),
        )

    def _resolve_scope(
            self,
            *,
            seed: EntityCandidate,
            scope: CandidateResolutionScope,
    ) -> tuple[EntityCandidate, ...]:
        if scope is CandidateResolutionScope.SINGLE:
            return (seed,)
        return tuple(
            self._session.scalars(
                select(EntityCandidate)
                .where(
                    EntityCandidate.entity_type == seed.entity_type,
                    EntityCandidate.canonical_text
                    == seed.canonical_text,
                )
                .order_by(EntityCandidate.id.asc())
            ).all()
        )

    def _validate_provenance(
            self,
            candidates: tuple[EntityCandidate, ...],
    ) -> None:
        mention_ids = {
            candidate.entity_mention_id for candidate in candidates
        }
        mentions = {
            mention.id: mention
            for mention in self._session.scalars(
                select(EntityMention).where(
                    EntityMention.id.in_(mention_ids)
                )
            )
        }
        for candidate in candidates:
            _, issue = resolve_entity_candidate_provenance(
                self._session,
                candidate=candidate,
                mention=mentions.get(candidate.entity_mention_id),
                document_version_id=candidate.document_version_id,
            )
            if issue is not None:
                raise ValueError(
                    "Candidate resolution provenance is invalid: "
                    f"candidate_id={candidate.id} issue={issue}"
                )

    @staticmethod
    def _result(
            *,
            row,
            entity: Entity,
            entity_created: bool,
            matched_candidates: tuple[EntityCandidate, ...],
            newly_assigned_candidate_ids: tuple[int, ...],
    ) -> CandidateResolutionResult:
        return CandidateResolutionResult(
            decision_id=row.id,
            revision=row.revision,
            supersedes_decision_id=(
                row.supersedes_candidate_resolution_decision_id
            ),
            status=row.status,
            scope=row.scope,
            seed_entity_candidate_id=row.seed_entity_candidate_id,
            entity_id=entity.id,
            entity_type=entity.entity_type.value,
            canonical_name=entity.canonical_name,
            entity_created=entity_created,
            matched_candidate_ids=tuple(
                candidate.id for candidate in matched_candidates
            ),
            newly_assigned_candidate_ids=tuple(
                sorted(newly_assigned_candidate_ids)
            ),
        )


def resolve_candidate_identity(
        *,
        candidate_id: int,
        decision: ManualCandidateResolutionDecision,
        entity_id: int | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> CandidateResolutionResult:
    """Commit one explicit candidate-resolution revision."""

    with session_factory() as session:
        try:
            result = CandidateResolutionService(session).decide(
                candidate_id=candidate_id,
                decision=decision,
                entity_id=entity_id,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
