from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import EntityType
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageReport,
    DocumentEntityCoverageStatus,
    get_document_entity_coverage,
)


class DocumentEntityReadinessStatus(str, Enum):
    """Downstream readiness state of one document entity projection."""

    READY = "ready"
    NO_CANDIDATES = "no_candidates"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class DocumentEntityReadinessReport:
    """Detached fail-closed entity readiness contract for one version."""

    document_version_id: int
    document_id: int
    version_number: int
    entity_type: EntityType | None
    status: DocumentEntityReadinessStatus
    ready_for_downstream_use: bool
    candidate_count: int
    safe_resolved_count: int
    unassigned_count: int
    blocked_count: int
    invalid_provenance_count: int
    not_entity_count: int = 0


def get_document_entity_readiness(
        *,
        document_version_id: int,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentEntityReadinessReport:
    """Evaluate exact entity-resolution readiness without thresholds."""

    coverage = get_document_entity_coverage(
        document_version_id=document_version_id,
        limit=1,
        entity_type=entity_type,
        session_factory=session_factory,
    )
    return evaluate_document_entity_readiness(
        coverage,
        entity_type=entity_type,
    )


def evaluate_document_entity_readiness(
        coverage: DocumentEntityCoverageReport,
        *,
        entity_type: EntityType | None = None,
) -> DocumentEntityReadinessReport:
    """Convert complete candidate coverage into a readiness contract."""

    counts = {
        item.status: item.count
        for item in coverage.counts_by_status
    }
    safe_resolved_count = counts.get(
        DocumentEntityCoverageStatus.SAFE_RESOLVED,
        0,
    )
    not_entity_count = counts.get(
        DocumentEntityCoverageStatus.NOT_ENTITY,
        0,
    )
    unassigned_count = counts.get(
        DocumentEntityCoverageStatus.UNASSIGNED,
        0,
    )
    blocked_count = counts.get(
        DocumentEntityCoverageStatus.BLOCKED,
        0,
    )
    invalid_provenance_count = counts.get(
        DocumentEntityCoverageStatus.INVALID_PROVENANCE,
        0,
    )
    status = _readiness_status(
        candidate_count=coverage.candidate_count,
        unassigned_count=unassigned_count,
        blocked_count=blocked_count,
        invalid_provenance_count=invalid_provenance_count,
    )

    return DocumentEntityReadinessReport(
        document_version_id=coverage.document_version_id,
        document_id=coverage.document_id,
        version_number=coverage.version_number,
        entity_type=entity_type,
        status=status,
        ready_for_downstream_use=(
            status is DocumentEntityReadinessStatus.READY
        ),
        candidate_count=coverage.candidate_count,
        safe_resolved_count=safe_resolved_count,
        unassigned_count=unassigned_count,
        blocked_count=blocked_count,
        invalid_provenance_count=invalid_provenance_count,
        not_entity_count=not_entity_count,
    )


def require_document_entity_readiness(
        *,
        document_version_id: int,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentEntityReadinessReport:
    """Return a ready report or reject unsafe downstream consumption."""

    report = get_document_entity_readiness(
        document_version_id=document_version_id,
        entity_type=entity_type,
        session_factory=session_factory,
    )
    if not report.ready_for_downstream_use:
        raise ValueError(
            "Document entity resolution is not ready for downstream use: "
            f"document_version_id={report.document_version_id} "
            f"status={report.status.value}."
        )
    return report


def _readiness_status(
        *,
        candidate_count: int,
        unassigned_count: int,
        blocked_count: int,
        invalid_provenance_count: int,
) -> DocumentEntityReadinessStatus:
    if invalid_provenance_count:
        return DocumentEntityReadinessStatus.INVALID
    if blocked_count:
        return DocumentEntityReadinessStatus.BLOCKED
    if unassigned_count:
        return DocumentEntityReadinessStatus.INCOMPLETE
    if candidate_count == 0:
        return DocumentEntityReadinessStatus.NO_CANDIDATES
    return DocumentEntityReadinessStatus.READY
