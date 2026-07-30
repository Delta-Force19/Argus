from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

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


class DocumentEntityCoverageStatus(str, Enum):
    """Resolution coverage state of one document entity candidate."""

    SAFE_RESOLVED = "safe_resolved"
    UNASSIGNED = "unassigned"
    BLOCKED = "blocked"
    INVALID_PROVENANCE = "invalid_provenance"


@dataclass(frozen=True, slots=True)
class DocumentEntityCoverageCount:
    """Count of document candidates in one coverage state."""

    status: DocumentEntityCoverageStatus
    count: int


@dataclass(frozen=True, slots=True)
class DocumentEntityCoverageItem:
    """Detached coverage result for one candidate observation."""

    entity_candidate_id: int
    entity_mention_id: int
    derived_artifact_id: int
    entity_type: EntityType
    canonical_text: str
    surface_text: str | None
    normalized_text: str | None
    start_char: int | None
    end_char: int | None
    status: DocumentEntityCoverageStatus
    entity_id: int | None
    entity_canonical_name: str | None
    assigned_by_alias_decision_id: int | None
    blocking_validities: tuple[EntityResolutionValidity, ...]
    provenance_issue: str | None


@dataclass(frozen=True, slots=True)
class DocumentEntityCoverageReport:
    """Complete counts and bounded evidence for one document version."""

    document_version_id: int
    document_id: int
    version_number: int
    candidate_count: int
    counts_by_status: tuple[DocumentEntityCoverageCount, ...]
    items: tuple[DocumentEntityCoverageItem, ...]


@dataclass(frozen=True, slots=True)
class _CoverageRow:
    candidate: EntityCandidate
    mention: EntityMention | None
    assignment: EntityCandidateAssignment | None
    entity: Entity | None


def get_document_entity_coverage(
        *,
        document_version_id: int,
        limit: int = 50,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentEntityCoverageReport:
    """Audit resolution coverage for every candidate in one version."""

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

        validity = evaluate_entity_registry_validity(session)
        blocking_by_entity = _blocking_validities_by_entity(
            validity.items
        )
        rows = _load_rows(
            session,
            document_version_id=document_version_id,
            entity_type=entity_type,
        )
        items = tuple(
            _classify_row(
                row,
                document_version_id=document_version_id,
                safe_entity_ids=validity.safe_entity_ids,
                blocked_entity_ids=validity.blocked_entity_ids,
                blocking_by_entity=blocking_by_entity,
            )
            for row in rows
        )
        counts = Counter(item.status for item in items)

    return DocumentEntityCoverageReport(
        document_version_id=document_version.id,
        document_id=document_version.document_id,
        version_number=document_version.version_number,
        candidate_count=len(items),
        counts_by_status=tuple(
            DocumentEntityCoverageCount(
                status=status,
                count=counts.get(status, 0),
            )
            for status in DocumentEntityCoverageStatus
        ),
        items=items[:limit],
    )


def _load_rows(
        session: Session,
        *,
        document_version_id: int,
        entity_type: EntityType | None,
) -> tuple[_CoverageRow, ...]:
    statement = (
        select(
            EntityCandidate,
            EntityMention,
            EntityCandidateAssignment,
            Entity,
        )
        .outerjoin(
            EntityMention,
            EntityMention.id == EntityCandidate.entity_mention_id,
        )
        .outerjoin(
            EntityCandidateAssignment,
            EntityCandidateAssignment.entity_candidate_id
            == EntityCandidate.id,
        )
        .outerjoin(
            Entity,
            Entity.id == EntityCandidateAssignment.entity_id,
        )
        .where(
            EntityCandidate.document_version_id
            == document_version_id
        )
        .order_by(
            EntityMention.start_char.asc().nulls_last(),
            EntityMention.end_char.asc().nulls_last(),
            EntityCandidate.id.asc(),
        )
    )
    if entity_type is not None:
        statement = statement.where(
            EntityCandidate.entity_type == entity_type
        )

    return tuple(
        _CoverageRow(
            candidate=candidate,
            mention=mention,
            assignment=assignment,
            entity=entity,
        )
        for candidate, mention, assignment, entity
        in session.execute(statement).all()
    )


def _blocking_validities_by_entity(
        items: tuple[EntityRegistryAuditItem, ...],
) -> dict[int, tuple[EntityResolutionValidity, ...]]:
    grouped: dict[int, set[EntityResolutionValidity]] = defaultdict(set)
    for item in items:
        if item.safe_for_downstream_use:
            continue
        grouped[item.entity_id].add(item.validity)
    return {
        entity_id: tuple(
            validity
            for validity in EntityResolutionValidity
            if validity in values
        )
        for entity_id, values in grouped.items()
    }


def _classify_row(
        row: _CoverageRow,
        *,
        document_version_id: int,
        safe_entity_ids: tuple[int, ...],
        blocked_entity_ids: tuple[int, ...],
        blocking_by_entity: dict[
            int,
            tuple[EntityResolutionValidity, ...],
        ],
) -> DocumentEntityCoverageItem:
    issue = _provenance_issue(
        row,
        document_version_id=document_version_id,
    )
    entity_id = (
        row.assignment.entity_id
        if row.assignment is not None
        else None
    )

    if issue is not None:
        status = DocumentEntityCoverageStatus.INVALID_PROVENANCE
    elif row.assignment is None:
        status = DocumentEntityCoverageStatus.UNASSIGNED
    elif entity_id in safe_entity_ids:
        status = DocumentEntityCoverageStatus.SAFE_RESOLVED
    elif entity_id in blocked_entity_ids:
        status = DocumentEntityCoverageStatus.BLOCKED
    else:
        status = DocumentEntityCoverageStatus.INVALID_PROVENANCE
        issue = (
            "Assigned entity is absent from the registry validity "
            "snapshot."
        )

    mention = row.mention
    return DocumentEntityCoverageItem(
        entity_candidate_id=row.candidate.id,
        entity_mention_id=row.candidate.entity_mention_id,
        derived_artifact_id=row.candidate.derived_artifact_id,
        entity_type=row.candidate.entity_type,
        canonical_text=row.candidate.canonical_text,
        surface_text=(
            mention.surface_text if mention is not None else None
        ),
        normalized_text=(
            mention.normalized_text if mention is not None else None
        ),
        start_char=mention.start_char if mention is not None else None,
        end_char=mention.end_char if mention is not None else None,
        status=status,
        entity_id=entity_id,
        entity_canonical_name=(
            row.entity.canonical_name
            if row.entity is not None
            else None
        ),
        assigned_by_alias_decision_id=(
            row.assignment.assigned_by_alias_decision_id
            if row.assignment is not None
            else None
        ),
        blocking_validities=(
            blocking_by_entity.get(entity_id, ())
            if status is DocumentEntityCoverageStatus.BLOCKED
            else ()
        ),
        provenance_issue=issue,
    )


def _provenance_issue(
        row: _CoverageRow,
        *,
        document_version_id: int,
) -> str | None:
    mention = row.mention
    if mention is None:
        return "Entity candidate references a missing mention."
    if mention.document_version_id != document_version_id:
        return "Entity candidate and mention use different documents."
    if (
        mention.derived_artifact_id
        != row.candidate.derived_artifact_id
    ):
        return "Entity candidate and mention use different artifacts."
    if mention.entity_type is not row.candidate.entity_type:
        return "Entity candidate and mention use different types."
    if row.assignment is not None and row.entity is None:
        return "Entity candidate assignment references a missing entity."
    if (
        row.entity is not None
        and row.entity.entity_type is not row.candidate.entity_type
    ):
        return "Entity candidate and assigned entity use different types."
    return None
