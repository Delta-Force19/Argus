from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.services.corpus_entity_readiness_service import (
    get_corpus_entity_readiness,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
)


@dataclass(frozen=True, slots=True)
class ReadyDocumentVersion:
    """Detached document version admitted to entity-dependent analysis."""

    document_version_id: int
    document_id: int
    version_number: int
    entity_type: EntityType | None
    candidate_count: int
    safe_resolved_count: int
    not_entity_count: int = 0


@dataclass(frozen=True, slots=True)
class ReadyDocumentSelection:
    """Complete ready count with a bounded downstream-safe selection."""

    entity_type: EntityType | None
    ready_document_version_count: int
    selected_document_version_count: int
    items: tuple[ReadyDocumentVersion, ...]


def select_ready_document_versions(
        *,
        limit: int = 50,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> ReadyDocumentSelection:
    """Select only versions that satisfy the exact readiness contract."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    readiness = get_corpus_entity_readiness(
        limit=limit,
        status=DocumentEntityReadinessStatus.READY,
        entity_type=entity_type,
        session_factory=session_factory,
    )
    items = tuple(
        _to_ready_document_version(
            report,
            entity_type=entity_type,
        )
        for report in readiness.items
    )
    if (
        readiness.matched_document_version_count
        != readiness.ready_document_version_count
    ):
        raise ValueError(
            "Corpus readiness returned inconsistent ready counts."
        )

    return ReadyDocumentSelection(
        entity_type=entity_type,
        ready_document_version_count=(
            readiness.ready_document_version_count
        ),
        selected_document_version_count=len(items),
        items=items,
    )


def _to_ready_document_version(
        report: DocumentEntityReadinessReport,
        *,
        entity_type: EntityType | None,
) -> ReadyDocumentVersion:
    if (
        report.status is not DocumentEntityReadinessStatus.READY
        or not report.ready_for_downstream_use
        or report.entity_type is not entity_type
        or report.candidate_count < 1
        or (
            report.safe_resolved_count + report.not_entity_count
            != report.candidate_count
        )
        or report.unassigned_count
        or report.blocked_count
        or report.invalid_provenance_count
    ):
        raise ValueError(
            "Corpus readiness returned an unsafe document version: "
            f"{report.document_version_id}."
        )

    return ReadyDocumentVersion(
        document_version_id=report.document_version_id,
        document_id=report.document_id,
        version_number=report.version_number,
        entity_type=entity_type,
        candidate_count=report.candidate_count,
        safe_resolved_count=report.safe_resolved_count,
        not_entity_count=report.not_entity_count,
    )
