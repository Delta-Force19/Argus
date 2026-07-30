from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.models import (
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
)
from argus.services.entity_registry_audit_service import (
    CandidateResolutionAuditItem,
    EntityRegistryAuditItem,
    EntityResolutionValidity,
    evaluate_entity_registry_validity,
)


@dataclass(frozen=True, slots=True)
class SafeEntityCandidate:
    """One assigned candidate observation exposed by the safe boundary."""

    assignment_id: int
    entity_candidate_id: int
    entity_type: EntityType
    canonical_text: str
    document_version_id: int
    derived_artifact_id: int
    entity_mention_id: int
    assigned_by_alias_decision_id: int | None
    assigned_by_candidate_resolution_decision_id: int | None = None


@dataclass(frozen=True, slots=True)
class ActiveEntityResolution:
    """One currently active proposal link supporting an entity."""

    proposal_id: int
    left_candidate_id: int
    right_candidate_id: int
    latest_alias_decision_id: int
    latest_revision: int


@dataclass(frozen=True, slots=True)
class ActiveCandidateResolution:
    """One active direct candidate-resolution link."""

    seed_candidate_id: int
    scope: str
    latest_candidate_resolution_decision_id: int
    latest_revision: int


@dataclass(frozen=True, slots=True)
class SafeEntity:
    """Detached persistent identity safe for analytical consumption."""

    entity_id: int
    entity_type: EntityType
    canonical_name: str
    canonical_entity_candidate_id: int
    created_from_alias_decision_id: int | None
    candidates: tuple[SafeEntityCandidate, ...]
    active_resolutions: tuple[ActiveEntityResolution, ...]
    created_from_candidate_resolution_decision_id: int | None = None
    active_candidate_resolutions: tuple[
        ActiveCandidateResolution,
        ...,
    ] = ()


@dataclass(frozen=True, slots=True)
class SafeEntityProjection:
    """Bounded view containing only fully active entity identities."""

    safe_entity_count: int
    items: tuple[SafeEntity, ...]


def get_safe_entity_projection(
        *,
        limit: int = 50,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> SafeEntityProjection:
    """Return only entities whose complete resolution history is active."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    with session_factory() as session:
        snapshot = evaluate_entity_registry_validity(session)
        safe_ids = snapshot.safe_entity_ids
        if not safe_ids:
            return SafeEntityProjection(
                safe_entity_count=0,
                items=(),
            )

        entity_statement = (
            select(Entity)
            .where(Entity.id.in_(safe_ids))
            .order_by(Entity.id.asc())
        )
        if entity_type is not None:
            entity_statement = entity_statement.where(
                Entity.entity_type == entity_type
            )
        entities = tuple(session.scalars(entity_statement).all())
        selected_entities = entities[:limit]
        selected_ids = tuple(entity.id for entity in selected_entities)

        assignments = _load_assignments(
            session,
            entity_ids=selected_ids,
        )
        candidates = _load_candidates(
            session,
            candidate_ids=tuple(
                assignment.entity_candidate_id
                for assignment in assignments
            ),
        )
        links_by_entity = _active_links_by_entity(snapshot.items)
        candidate_links_by_entity = _active_candidate_links_by_entity(
            snapshot.candidate_items
        )
        items = _build_safe_entities(
            entities=selected_entities,
            assignments=assignments,
            candidates=candidates,
            links_by_entity=links_by_entity,
            candidate_links_by_entity=candidate_links_by_entity,
        )

    return SafeEntityProjection(
        safe_entity_count=len(entities),
        items=items,
    )


def _load_assignments(
        session: Session,
        *,
        entity_ids: tuple[int, ...],
) -> tuple[EntityCandidateAssignment, ...]:
    if not entity_ids:
        return ()
    return tuple(
        session.scalars(
            select(EntityCandidateAssignment)
            .where(
                EntityCandidateAssignment.entity_id.in_(entity_ids)
            )
            .order_by(
                EntityCandidateAssignment.entity_id.asc(),
                EntityCandidateAssignment.entity_candidate_id.asc(),
            )
        ).all()
    )


def _load_candidates(
        session: Session,
        *,
        candidate_ids: tuple[int, ...],
) -> dict[int, EntityCandidate]:
    if not candidate_ids:
        return {}
    return {
        candidate.id: candidate
        for candidate in session.scalars(
            select(EntityCandidate).where(
                EntityCandidate.id.in_(candidate_ids)
            )
        )
    }


def _active_links_by_entity(
        items: tuple[EntityRegistryAuditItem, ...],
) -> dict[int, tuple[EntityRegistryAuditItem, ...]]:
    grouped: dict[int, list[EntityRegistryAuditItem]] = defaultdict(list)
    for item in items:
        if not item.safe_for_downstream_use:
            continue
        if item.validity is not EntityResolutionValidity.ACTIVE:
            raise ValueError(
                "Safe entity registry item is not active."
            )
        grouped[item.entity_id].append(item)
    return {
        entity_id: tuple(entity_items)
        for entity_id, entity_items in grouped.items()
    }


def _active_candidate_links_by_entity(
        items: tuple[CandidateResolutionAuditItem, ...],
) -> dict[int, tuple[CandidateResolutionAuditItem, ...]]:
    grouped: dict[int, list[CandidateResolutionAuditItem]] = defaultdict(list)
    for item in items:
        if not item.safe_for_downstream_use:
            continue
        if item.validity is not EntityResolutionValidity.ACTIVE:
            raise ValueError(
                "Safe candidate resolution is not active."
            )
        grouped[item.entity_id].append(item)
    return {
        entity_id: tuple(entity_items)
        for entity_id, entity_items in grouped.items()
    }


def _build_safe_entities(
        *,
        entities: tuple[Entity, ...],
        assignments: tuple[EntityCandidateAssignment, ...],
        candidates: dict[int, EntityCandidate],
        links_by_entity: dict[int, tuple[EntityRegistryAuditItem, ...]],
        candidate_links_by_entity: dict[
            int,
            tuple[CandidateResolutionAuditItem, ...],
        ],
) -> tuple[SafeEntity, ...]:
    assignments_by_entity: dict[
        int,
        list[EntityCandidateAssignment],
    ] = defaultdict(list)
    for assignment in assignments:
        assignments_by_entity[assignment.entity_id].append(assignment)

    projected: list[SafeEntity] = []
    for entity in entities:
        entity_assignments = assignments_by_entity.get(entity.id, [])
        entity_links = links_by_entity.get(entity.id, ())
        candidate_links = candidate_links_by_entity.get(entity.id, ())
        if (
                not entity_assignments
                or not (entity_links or candidate_links)
        ):
            raise ValueError(
                "Safe entity is missing assignments or active evidence."
            )

        projected_candidates = tuple(
            _project_candidate(
                entity=entity,
                assignment=assignment,
                candidate=candidates.get(
                    assignment.entity_candidate_id
                ),
            )
            for assignment in entity_assignments
        )
        if entity.canonical_entity_candidate_id not in {
            candidate.entity_candidate_id
            for candidate in projected_candidates
        }:
            raise ValueError(
                "Safe entity canonical candidate is not assigned."
            )

        projected.append(
            SafeEntity(
                entity_id=entity.id,
                entity_type=entity.entity_type,
                canonical_name=entity.canonical_name,
                canonical_entity_candidate_id=(
                    entity.canonical_entity_candidate_id
                ),
                created_from_alias_decision_id=(
                    entity.created_from_alias_decision_id
                ),
                created_from_candidate_resolution_decision_id=(
                    entity
                    .created_from_candidate_resolution_decision_id
                ),
                candidates=projected_candidates,
                active_resolutions=tuple(
                    ActiveEntityResolution(
                        proposal_id=item.proposal_id,
                        left_candidate_id=item.left_candidate_id,
                        right_candidate_id=item.right_candidate_id,
                        latest_alias_decision_id=(
                            item.latest_decision_id
                        ),
                        latest_revision=item.latest_revision,
                    )
                    for item in entity_links
                ),
                active_candidate_resolutions=tuple(
                    ActiveCandidateResolution(
                        seed_candidate_id=item.seed_candidate_id,
                        scope=item.scope.value,
                        latest_candidate_resolution_decision_id=(
                            item.latest_decision_id
                        ),
                        latest_revision=item.latest_revision,
                    )
                    for item in candidate_links
                ),
            )
        )
    return tuple(projected)


def _project_candidate(
        *,
        entity: Entity,
        assignment: EntityCandidateAssignment,
        candidate: EntityCandidate | None,
) -> SafeEntityCandidate:
    if candidate is None:
        raise ValueError(
            "Safe entity assignment references a missing candidate."
        )
    if candidate.entity_type is not entity.entity_type:
        raise ValueError(
            "Safe entity candidate type does not match the entity."
        )
    return SafeEntityCandidate(
        assignment_id=assignment.id,
        entity_candidate_id=candidate.id,
        entity_type=candidate.entity_type,
        canonical_text=candidate.canonical_text,
        document_version_id=candidate.document_version_id,
        derived_artifact_id=candidate.derived_artifact_id,
        entity_mention_id=candidate.entity_mention_id,
        assigned_by_alias_decision_id=(
            assignment.assigned_by_alias_decision_id
        ),
        assigned_by_candidate_resolution_decision_id=(
            assignment
            .assigned_by_candidate_resolution_decision_id
        ),
    )
