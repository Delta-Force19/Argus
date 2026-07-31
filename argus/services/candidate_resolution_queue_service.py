from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.models import (
    Document,
    DocumentVersion,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityMention,
)
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageStatus,
    evaluate_document_entity_coverage,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    evaluate_document_entity_readiness,
)
from argus.services.entity_registry_audit_service import (
    EntityRegistryValiditySnapshot,
    evaluate_entity_registry_validity,
)
from argus.services.entity_candidate_provenance_service import (
    resolve_entity_candidate_provenance,
)


class ExactCanonicalScopeState(str, Enum):
    """Existing assignment state across one exact-canonical scope."""

    NEW_ENTITY = "new_entity"
    EXTENDS_ENTITY = "extends_entity"
    INVALID_PROVENANCE = "invalid_provenance"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CandidateResolutionContext:
    """One bounded source context for a queue group."""

    entity_candidate_id: int
    entity_mention_id: int
    surface_text: str
    start_char: int
    end_char: int
    context_text: str


@dataclass(frozen=True, slots=True)
class CandidateResolutionQueueGroup:
    """One explicit identity decision that can advance the document."""

    entity_type: EntityType
    canonical_text: str
    seed_entity_candidate_id: int
    document_candidate_count: int
    corpus_candidate_count: int
    corpus_unassigned_count: int
    corpus_invalid_provenance_count: int
    surface_variants: tuple[str, ...]
    exact_scope_state: ExactCanonicalScopeState
    assigned_entity_ids: tuple[int, ...]
    contexts: tuple[CandidateResolutionContext, ...]
    corpus_not_entity_count: int = 0


@dataclass(frozen=True, slots=True)
class CandidateResolutionQueue:
    """Actionable unresolved groups for one selected document version."""

    document_version_id: int
    document_id: int
    version_number: int
    title: str | None
    language: str | None
    identifier_value: str
    readiness: DocumentEntityReadinessReport
    unresolved_group_count: int
    shown_group_count: int
    groups: tuple[CandidateResolutionQueueGroup, ...]


@dataclass(frozen=True, slots=True)
class _CandidateRow:
    candidate: EntityCandidate
    mention: EntityMention
    assigned_entity_id: int | None


def get_candidate_resolution_queue(
        *,
        document_version_id: int | None = None,
        limit: int = 20,
        contexts_per_group: int = 2,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> CandidateResolutionQueue:
    """Select one actionable document and group its unresolved candidates."""

    if document_version_id is not None and document_version_id < 1:
        raise ValueError("document_version_id must be greater than zero.")
    if limit < 1:
        raise ValueError("limit must be greater than zero.")
    if contexts_per_group < 1:
        raise ValueError("contexts_per_group must be greater than zero.")

    with session_factory() as session:
        validity = evaluate_entity_registry_validity(session)
        document_version, readiness = _select_document(
            session,
            validity=validity,
            document_version_id=document_version_id,
            entity_type=entity_type,
        )
        document = session.get(Document, document_version.document_id)
        if document is None:
            raise ValueError(
                "Document version references a missing document: "
                f"{document_version.id}."
            )

        rows = _load_candidate_rows(
            session,
            document_version_id=document_version.id,
            entity_type=entity_type,
        )
        unresolved_candidate_ids = _unresolved_candidate_ids(
            session,
            validity=validity,
            document_version=document_version,
            entity_type=entity_type,
        )
        groups = _build_groups(
            session,
            rows=rows,
            unresolved_candidate_ids=unresolved_candidate_ids,
            contexts_per_group=contexts_per_group,
            not_entity_candidate_ids=set(
                validity.not_entity_candidate_ids
            ),
            invalid_not_entity_candidate_ids=set(
                validity.invalid_not_entity_candidate_ids
            ),
        )

    return CandidateResolutionQueue(
        document_version_id=document_version.id,
        document_id=document.id,
        version_number=document_version.version_number,
        title=document.title,
        language=document.language,
        identifier_value=document.identifier_value,
        readiness=readiness,
        unresolved_group_count=len(groups),
        shown_group_count=min(len(groups), limit),
        groups=groups[:limit],
    )


def _select_document(
        session: Session,
        *,
        validity: EntityRegistryValiditySnapshot,
        document_version_id: int | None,
        entity_type: EntityType | None,
) -> tuple[DocumentVersion, DocumentEntityReadinessReport]:
    if document_version_id is not None:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                "Document version does not exist: "
                f"{document_version_id}."
            )
        readiness = _readiness(
            session,
            validity=validity,
            document_version=version,
            entity_type=entity_type,
        )
        return version, readiness

    actionable: list[
        tuple[DocumentVersion, DocumentEntityReadinessReport]
    ] = []
    for version in session.scalars(
        select(DocumentVersion).order_by(DocumentVersion.id.asc())
    ):
        readiness = _readiness(
            session,
            validity=validity,
            document_version=version,
            entity_type=entity_type,
        )
        if readiness.unassigned_count:
            actionable.append((version, readiness))
    if not actionable:
        raise ValueError(
            "No document version has unassigned entity candidates."
        )

    return min(
        actionable,
        key=lambda item: (
            bool(
                item[1].blocked_count
                or item[1].invalid_provenance_count
            ),
            item[1].blocked_count + item[1].invalid_provenance_count,
            item[1].unassigned_count,
            item[1].candidate_count,
            item[0].id,
        ),
    )


def _readiness(
        session: Session,
        *,
        validity: EntityRegistryValiditySnapshot,
        document_version: DocumentVersion,
        entity_type: EntityType | None,
) -> DocumentEntityReadinessReport:
    coverage = evaluate_document_entity_coverage(
        session,
        document_version=document_version,
        validity=validity,
        limit=1,
        entity_type=entity_type,
    )
    return evaluate_document_entity_readiness(
        coverage,
        entity_type=entity_type,
    )


def _unresolved_candidate_ids(
        session: Session,
        *,
        validity: EntityRegistryValiditySnapshot,
        document_version: DocumentVersion,
        entity_type: EntityType | None,
) -> set[int]:
    coverage = evaluate_document_entity_coverage(
        session,
        document_version=document_version,
        validity=validity,
        limit=2**31 - 1,
        entity_type=entity_type,
    )
    return {
        item.entity_candidate_id
        for item in coverage.items
        if item.status is DocumentEntityCoverageStatus.UNASSIGNED
    }


def _load_candidate_rows(
        session: Session,
        *,
        document_version_id: int | None = None,
        entity_type: EntityType | None = None,
) -> tuple[_CandidateRow, ...]:
    statement = (
        select(
            EntityCandidate,
            EntityMention,
            EntityCandidateAssignment.entity_id,
        )
        .join(
            EntityMention,
            EntityMention.id == EntityCandidate.entity_mention_id,
        )
        .outerjoin(
            EntityCandidateAssignment,
            EntityCandidateAssignment.entity_candidate_id
            == EntityCandidate.id,
        )
        .order_by(
            EntityCandidate.entity_type.asc(),
            EntityCandidate.canonical_text.asc(),
            EntityMention.start_char.asc(),
            EntityCandidate.id.asc(),
        )
    )
    if document_version_id is not None:
        statement = statement.where(
            EntityCandidate.document_version_id == document_version_id
        )
    if entity_type is not None:
        statement = statement.where(
            EntityCandidate.entity_type == entity_type
        )
    return tuple(
        _CandidateRow(
            candidate=candidate,
            mention=mention,
            assigned_entity_id=assigned_entity_id,
        )
        for candidate, mention, assigned_entity_id
        in session.execute(statement)
    )


def _build_groups(
        session: Session,
        *,
        rows: tuple[_CandidateRow, ...],
        unresolved_candidate_ids: set[int],
        contexts_per_group: int,
        not_entity_candidate_ids: set[int],
        invalid_not_entity_candidate_ids: set[int],
) -> tuple[CandidateResolutionQueueGroup, ...]:
    document_groups: dict[
        tuple[EntityType, str],
        list[_CandidateRow],
    ] = defaultdict(list)
    for row in rows:
        key = (row.candidate.entity_type, row.candidate.canonical_text)
        if row.candidate.id in unresolved_candidate_ids:
            document_groups[key].append(row)

    corpus_rows = _load_candidate_rows(session)
    corpus_groups: dict[
        tuple[EntityType, str],
        list[_CandidateRow],
    ] = defaultdict(list)
    for row in corpus_rows:
        key = (row.candidate.entity_type, row.candidate.canonical_text)
        if key in document_groups:
            corpus_groups[key].append(row)

    groups: list[CandidateResolutionQueueGroup] = []
    for key, document_rows in document_groups.items():
        matching_corpus_rows = corpus_groups[key]
        assigned_entity_ids = tuple(sorted({
            row.assigned_entity_id
            for row in matching_corpus_rows
            if row.assigned_entity_id is not None
        }))
        invalid_provenance_count = sum(
            resolve_entity_candidate_provenance(
                session,
                candidate=row.candidate,
                mention=row.mention,
                document_version_id=row.candidate.document_version_id,
            )[1]
            is not None
            for row in matching_corpus_rows
        )
        not_entity_count = sum(
            row.candidate.id in not_entity_candidate_ids
            for row in matching_corpus_rows
        )
        invalid_not_entity_count = sum(
            row.candidate.id in invalid_not_entity_candidate_ids
            for row in matching_corpus_rows
        )
        if invalid_provenance_count or invalid_not_entity_count:
            scope_state = ExactCanonicalScopeState.INVALID_PROVENANCE
        elif not_entity_count:
            scope_state = ExactCanonicalScopeState.CONFLICT
        elif not assigned_entity_ids:
            scope_state = ExactCanonicalScopeState.NEW_ENTITY
        elif len(assigned_entity_ids) == 1:
            scope_state = ExactCanonicalScopeState.EXTENDS_ENTITY
        else:
            scope_state = ExactCanonicalScopeState.CONFLICT
        groups.append(
            CandidateResolutionQueueGroup(
                entity_type=key[0],
                canonical_text=key[1],
                seed_entity_candidate_id=min(
                    row.candidate.id for row in document_rows
                ),
                document_candidate_count=len(document_rows),
                corpus_candidate_count=len(matching_corpus_rows),
                corpus_unassigned_count=sum(
                    row.assigned_entity_id is None
                    and row.candidate.id not in not_entity_candidate_ids
                    for row in matching_corpus_rows
                ),
                corpus_invalid_provenance_count=(
                    invalid_provenance_count
                ),
                surface_variants=tuple(sorted({
                    row.mention.surface_text for row in document_rows
                })),
                exact_scope_state=scope_state,
                assigned_entity_ids=assigned_entity_ids,
                contexts=tuple(
                    CandidateResolutionContext(
                        entity_candidate_id=row.candidate.id,
                        entity_mention_id=row.mention.id,
                        surface_text=row.mention.surface_text,
                        start_char=row.mention.start_char,
                        end_char=row.mention.end_char,
                        context_text=_normalize_context(
                            row.candidate.context_text
                        ),
                    )
                    for row in document_rows[:contexts_per_group]
                ),
                corpus_not_entity_count=not_entity_count,
            )
        )
    return tuple(sorted(
        groups,
        key=lambda item: (
            item.exact_scope_state in {
                ExactCanonicalScopeState.INVALID_PROVENANCE,
                ExactCanonicalScopeState.CONFLICT,
            },
            -item.corpus_unassigned_count,
            item.entity_type.value,
            item.canonical_text,
            item.seed_entity_candidate_id,
        ),
    ))


def _normalize_context(value: str, *, limit: int = 280) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
