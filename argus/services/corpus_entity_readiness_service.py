from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.services.document_entity_coverage_service import (
    get_document_entity_coverage_batch,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
    evaluate_document_entity_readiness,
)


@dataclass(frozen=True, slots=True)
class CorpusEntityReadinessCount:
    """Count of document versions in one readiness state."""

    status: DocumentEntityReadinessStatus
    count: int


@dataclass(frozen=True, slots=True)
class CorpusEntityReadinessReport:
    """Complete corpus counts with a bounded document-level view."""

    entity_type: EntityType | None
    document_version_count: int
    ready_document_version_count: int
    unsafe_document_version_count: int
    matched_document_version_count: int
    candidate_count: int
    safe_resolved_count: int
    unassigned_count: int
    blocked_count: int
    invalid_provenance_count: int
    counts_by_status: tuple[CorpusEntityReadinessCount, ...]
    items: tuple[DocumentEntityReadinessReport, ...]
    not_entity_count: int = 0


def get_corpus_entity_readiness(
        *,
        limit: int = 50,
        status: DocumentEntityReadinessStatus | None = None,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> CorpusEntityReadinessReport:
    """Audit readiness of every document version in one DB snapshot."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    coverages = get_document_entity_coverage_batch(
        item_limit=1,
        entity_type=entity_type,
        session_factory=session_factory,
    )
    reports = tuple(
        evaluate_document_entity_readiness(
            coverage,
            entity_type=entity_type,
        )
        for coverage in coverages
    )
    counts = Counter(report.status for report in reports)
    matched = tuple(
        report
        for report in reports
        if status is None or report.status is status
    )

    return CorpusEntityReadinessReport(
        entity_type=entity_type,
        document_version_count=len(reports),
        ready_document_version_count=counts.get(
            DocumentEntityReadinessStatus.READY,
            0,
        ),
        unsafe_document_version_count=(
            len(reports)
            - counts.get(DocumentEntityReadinessStatus.READY, 0)
        ),
        matched_document_version_count=len(matched),
        candidate_count=sum(item.candidate_count for item in reports),
        safe_resolved_count=sum(
            item.safe_resolved_count for item in reports
        ),
        not_entity_count=sum(
            item.not_entity_count for item in reports
        ),
        unassigned_count=sum(
            item.unassigned_count for item in reports
        ),
        blocked_count=sum(item.blocked_count for item in reports),
        invalid_provenance_count=sum(
            item.invalid_provenance_count for item in reports
        ),
        counts_by_status=tuple(
            CorpusEntityReadinessCount(
                status=readiness_status,
                count=counts.get(readiness_status, 0),
            )
            for readiness_status in DocumentEntityReadinessStatus
        ),
        items=matched[:limit],
    )
