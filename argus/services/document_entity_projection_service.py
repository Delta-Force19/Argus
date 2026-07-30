from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.models import (
    DocumentVersion,
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityMention,
)
from argus.services.entity_registry_audit_service import (
    EntityRegistryAuditItem,
    EntityResolutionValidity,
    evaluate_entity_registry_validity,
)
from argus.services.safe_entity_projection_service import (
    ActiveEntityResolution,
)


@dataclass(frozen=True, slots=True)
class ResolvedEntityOccurrence:
    """One resolved mention occurrence in an exact document version."""

    entity_candidate_id: int
    entity_mention_id: int
    derived_artifact_id: int
    canonical_text: str
    surface_text: str
    normalized_text: str
    source_label: str
    start_char: int
    end_char: int
    assigned_by_alias_decision_id: int


@dataclass(frozen=True, slots=True)
class DocumentResolvedEntity:
    """One fully active identity observed in a document version."""

    entity_id: int
    entity_type: EntityType
    canonical_name: str
    canonical_entity_candidate_id: int
    occurrences: tuple[ResolvedEntityOccurrence, ...]
    active_resolutions: tuple[ActiveEntityResolution, ...]


@dataclass(frozen=True, slots=True)
class DocumentEntityProjection:
    """Detached resolved-entity view for one immutable document version."""

    document_version_id: int
    document_id: int
    version_number: int
    resolved_entity_count: int
    resolved_occurrence_count: int
    items: tuple[DocumentResolvedEntity, ...]


@dataclass(frozen=True, slots=True)
class _OccurrenceRow:
    entity: Entity
    assignment: EntityCandidateAssignment
    candidate: EntityCandidate
    mention: EntityMention


def get_document_entity_projection(
        *,
        document_version_id: int,
        limit: int = 50,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentEntityProjection:
    """Return fully active resolved identities mentioned in one version."""

    if document_version_id < 1:
        raise ValueError(
            "document_version_id must be greater than zero."
        )
    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    with session_factory() as session:
        document_version = session.get(
            DocumentVersion,
            document_version_id,
        )
        if document_version is None:
            raise ValueError(
                "Document version does not exist: "
                f"{document_version_id}."
            )

        snapshot = evaluate_entity_registry_validity(session)
        rows = _load_occurrence_rows(
            session,
            document_version_id=document_version_id,
            safe_entity_ids=snapshot.safe_entity_ids,
            entity_type=entity_type,
        )
        grouped = _group_occurrences(
            rows,
            document_version_id=document_version_id,
        )
        active_resolutions = _active_resolutions_by_entity(
            snapshot.items
        )
        entity_ids = tuple(sorted(grouped))
        selected_entity_ids = entity_ids[:limit]
        items = tuple(
            _project_entity(
                grouped[entity_id],
                active_resolutions=active_resolutions.get(
                    entity_id,
                    (),
                ),
            )
            for entity_id in selected_entity_ids
        )

    return DocumentEntityProjection(
        document_version_id=document_version.id,
        document_id=document_version.document_id,
        version_number=document_version.version_number,
        resolved_entity_count=len(entity_ids),
        resolved_occurrence_count=sum(
            len(entity_rows)
            for entity_rows in grouped.values()
        ),
        items=items,
    )


def _load_occurrence_rows(
        session: Session,
        *,
        document_version_id: int,
        safe_entity_ids: tuple[int, ...],
        entity_type: EntityType | None,
) -> tuple[_OccurrenceRow, ...]:
    if not safe_entity_ids:
        return ()

    statement = (
        select(
            Entity,
            EntityCandidateAssignment,
            EntityCandidate,
            EntityMention,
        )
        .join(
            EntityCandidateAssignment,
            EntityCandidateAssignment.entity_id == Entity.id,
        )
        .join(
            EntityCandidate,
            EntityCandidate.id
            == EntityCandidateAssignment.entity_candidate_id,
        )
        .join(
            EntityMention,
            EntityMention.id == EntityCandidate.entity_mention_id,
        )
        .where(
            Entity.id.in_(safe_entity_ids),
            EntityCandidate.document_version_id
            == document_version_id,
        )
        .order_by(
            Entity.id.asc(),
            EntityMention.start_char.asc(),
            EntityMention.end_char.asc(),
            EntityMention.id.asc(),
            EntityCandidate.id.asc(),
        )
    )
    if entity_type is not None:
        statement = statement.where(
            Entity.entity_type == entity_type
        )

    return tuple(
        _OccurrenceRow(
            entity=entity,
            assignment=assignment,
            candidate=candidate,
            mention=mention,
        )
        for entity, assignment, candidate, mention
        in session.execute(statement).all()
    )


def _group_occurrences(
        rows: tuple[_OccurrenceRow, ...],
        *,
        document_version_id: int,
) -> dict[int, tuple[_OccurrenceRow, ...]]:
    grouped: dict[int, list[_OccurrenceRow]] = defaultdict(list)
    for row in rows:
        _validate_occurrence(
            row,
            document_version_id=document_version_id,
        )
        grouped[row.entity.id].append(row)
    return {
        entity_id: tuple(entity_rows)
        for entity_id, entity_rows in grouped.items()
    }


def _validate_occurrence(
        row: _OccurrenceRow,
        *,
        document_version_id: int,
) -> None:
    if row.candidate.document_version_id != document_version_id:
        raise ValueError(
            "Resolved candidate belongs to another document version."
        )
    if row.mention.document_version_id != document_version_id:
        raise ValueError(
            "Resolved mention belongs to another document version."
        )
    if (
        row.candidate.derived_artifact_id
        != row.mention.derived_artifact_id
    ):
        raise ValueError(
            "Resolved candidate and mention use different artifacts."
        )
    if row.candidate.entity_type is not row.entity.entity_type:
        raise ValueError(
            "Resolved candidate type does not match the entity."
        )
    if row.mention.entity_type is not row.entity.entity_type:
        raise ValueError(
            "Resolved mention type does not match the entity."
        )


def _active_resolutions_by_entity(
        items: tuple[EntityRegistryAuditItem, ...],
) -> dict[int, tuple[ActiveEntityResolution, ...]]:
    grouped: dict[int, list[ActiveEntityResolution]] = defaultdict(list)
    for item in items:
        if not item.safe_for_downstream_use:
            continue
        if item.validity is not EntityResolutionValidity.ACTIVE:
            raise ValueError(
                "Safe document entity link is not active."
            )
        grouped[item.entity_id].append(
            ActiveEntityResolution(
                proposal_id=item.proposal_id,
                left_candidate_id=item.left_candidate_id,
                right_candidate_id=item.right_candidate_id,
                latest_alias_decision_id=item.latest_decision_id,
                latest_revision=item.latest_revision,
            )
        )
    return {
        entity_id: tuple(resolutions)
        for entity_id, resolutions in grouped.items()
    }


def _project_entity(
        rows: tuple[_OccurrenceRow, ...],
        *,
        active_resolutions: tuple[ActiveEntityResolution, ...],
) -> DocumentResolvedEntity:
    if not rows:
        raise ValueError(
            "Document entity projection requires an occurrence."
        )
    if not active_resolutions:
        raise ValueError(
            "Document entity projection is missing active evidence."
        )

    entity = rows[0].entity
    if any(row.entity.id != entity.id for row in rows):
        raise ValueError(
            "Document entity occurrence group contains mixed entities."
        )

    return DocumentResolvedEntity(
        entity_id=entity.id,
        entity_type=entity.entity_type,
        canonical_name=entity.canonical_name,
        canonical_entity_candidate_id=(
            entity.canonical_entity_candidate_id
        ),
        occurrences=tuple(
            ResolvedEntityOccurrence(
                entity_candidate_id=row.candidate.id,
                entity_mention_id=row.mention.id,
                derived_artifact_id=row.candidate.derived_artifact_id,
                canonical_text=row.candidate.canonical_text,
                surface_text=row.mention.surface_text,
                normalized_text=row.mention.normalized_text,
                source_label=row.mention.source_label,
                start_char=row.mention.start_char,
                end_char=row.mention.end_char,
                assigned_by_alias_decision_id=(
                    row.assignment.assigned_by_alias_decision_id
                ),
            )
            for row in rows
        ),
        active_resolutions=active_resolutions,
    )
