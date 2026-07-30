from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import AliasDecisionStatus
from argus.models import (
    AliasProposal,
    Entity,
    EntityCandidate,
)
from argus.storage.alias_decision_repository import (
    AliasDecisionRepository,
)
from argus.storage.entity_repository import (
    EntityCandidateAssignmentRepository,
    EntityRepository,
    EntityResolutionEvidenceRepository,
)


@dataclass(frozen=True, slots=True)
class EntityResolutionResult:
    """Detached identifiers produced by one explicit resolution."""

    entity_id: int
    entity_type: str
    canonical_name: str
    canonical_entity_candidate_id: int
    alias_decision_id: int
    assigned_candidate_ids: tuple[int, ...]
    entity_created: bool


class EntityResolutionService:
    """Consume one latest approved alias decision into the entity registry.

    The service never commits. The caller owns the surrounding transaction.
    It does not merge two existing entities or infer a canonical name.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._decisions = AliasDecisionRepository(session)
        self._entities = EntityRepository(session)
        self._assignments = EntityCandidateAssignmentRepository(session)
        self._evidence = EntityResolutionEvidenceRepository(session)

    def resolve_approved_alias(
            self,
            *,
            proposal_id: int,
            entity_id: int | None = None,
            canonical_candidate_id: int | None = None,
    ) -> EntityResolutionResult:
        if proposal_id < 1:
            raise ValueError("proposal_id must be positive.")
        if entity_id is not None and entity_id < 1:
            raise ValueError("entity_id must be positive.")
        if (
                canonical_candidate_id is not None
                and canonical_candidate_id < 1
        ):
            raise ValueError("canonical_candidate_id must be positive.")

        proposal = self._session.get(AliasProposal, proposal_id)
        if proposal is None:
            raise ValueError("Alias proposal does not exist.")

        decision = self._decisions.get_latest(proposal.id)
        if (
                decision is None
                or decision.status is not AliasDecisionStatus.APPROVED
        ):
            raise ValueError(
                "The latest alias decision must be approved."
            )

        candidate_ids = (
            proposal.left_entity_candidate_id,
            proposal.right_entity_candidate_id,
        )
        candidates = self._load_candidates(candidate_ids)
        if any(
                candidate.entity_type is not proposal.entity_type
                for candidate in candidates.values()
        ):
            raise ValueError(
                "Alias proposal type conflicts with its candidates."
            )
        if canonical_candidate_id is not None:
            if canonical_candidate_id not in candidate_ids:
                raise ValueError(
                    "canonical_candidate_id must belong to the proposal."
                )

        existing_evidence = self._evidence.get_by_decision(decision.id)
        if existing_evidence is not None:
            entity = self._session.get(
                Entity,
                existing_evidence.entity_id,
            )
            if entity is None:
                raise ValueError(
                    "Entity-resolution evidence references a missing entity."
                )
            if entity_id is not None and entity.id != entity_id:
                raise ValueError(
                    "Resolution already belongs to another entity."
                )
            if (
                    canonical_candidate_id is not None
                    and canonical_candidate_id
                    != entity.canonical_entity_candidate_id
            ):
                raise ValueError(
                    "Resolution conflicts with the entity's canonical "
                    "candidate."
                )
            return self._result(
                entity=entity,
                decision_id=decision.id,
                candidate_ids=candidate_ids,
                entity_created=False,
            )

        assignments = self._assignments.get_for_candidates(candidate_ids)
        assigned_entity_ids = {
            item.entity_id for item in assignments.values()
        }
        if len(assigned_entity_ids) > 1:
            raise ValueError(
                "Candidates belong to different entities; an explicit "
                "entity merge is required."
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

        entity_created = target_entity_id is None
        if entity_created:
            if canonical_candidate_id is None:
                raise ValueError(
                    "A new entity requires canonical_candidate_id."
                )
            entity = self._entities.create(
                canonical_candidate=candidates[canonical_candidate_id],
                creation_decision=decision,
            )
        else:
            entity = self._session.get(Entity, target_entity_id)
            if entity is None:
                raise ValueError("Entity does not exist.")
            if entity.entity_type is not proposal.entity_type:
                raise ValueError(
                    "Entity type conflicts with the alias proposal."
                )
            if canonical_candidate_id is not None:
                raise ValueError(
                    "canonical_candidate_id is only valid when creating "
                    "a new entity."
                )

        for candidate_id in candidate_ids:
            candidate = candidates[candidate_id]
            if candidate.entity_type is not entity.entity_type:
                raise ValueError(
                    "Candidate type conflicts with the target entity."
                )
            self._assignments.assign(
                entity=entity,
                candidate=candidate,
                decision=decision,
            )
        self._evidence.record(entity=entity, decision=decision)

        return self._result(
            entity=entity,
            decision_id=decision.id,
            candidate_ids=candidate_ids,
            entity_created=entity_created,
        )

    def _load_candidates(
            self,
            candidate_ids: tuple[int, int],
    ) -> dict[int, EntityCandidate]:
        candidates = {
            candidate_id: self._session.get(
                EntityCandidate,
                candidate_id,
            )
            for candidate_id in candidate_ids
        }
        if any(item is None for item in candidates.values()):
            raise ValueError(
                "Alias proposal references a missing entity candidate."
            )
        return {
            candidate_id: candidate
            for candidate_id, candidate in candidates.items()
            if candidate is not None
        }

    @staticmethod
    def _result(
            *,
            entity: Entity,
            decision_id: int,
            candidate_ids: tuple[int, int],
            entity_created: bool,
    ) -> EntityResolutionResult:
        return EntityResolutionResult(
            entity_id=entity.id,
            entity_type=entity.entity_type.value,
            canonical_name=entity.canonical_name,
            canonical_entity_candidate_id=(
                entity.canonical_entity_candidate_id
            ),
            alias_decision_id=decision_id,
            assigned_candidate_ids=tuple(sorted(candidate_ids)),
            entity_created=entity_created,
        )


def resolve_alias_identity(
        *,
        proposal_id: int,
        entity_id: int | None = None,
        canonical_candidate_id: int | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EntityResolutionResult:
    """Commit one explicit approved-alias resolution."""

    with session_factory() as session:
        try:
            result = EntityResolutionService(
                session
            ).resolve_approved_alias(
                proposal_id=proposal_id,
                entity_id=entity_id,
                canonical_candidate_id=canonical_candidate_id,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
