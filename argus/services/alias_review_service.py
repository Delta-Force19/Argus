from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from argus.database import SessionLocal
from argus.knowledge import (
    AliasDecisionStatus,
    AliasSignalType,
    EntityType,
    ManualAliasDecision,
)
from argus.models import AliasDecision, AliasProposal, EntityCandidate
from argus.services.alias_decision_service import AliasDecisionService


@dataclass(frozen=True, slots=True)
class AliasReviewQueueItem:
    """One open proposal with the evidence needed for manual review."""

    proposal_id: int
    document_version_id: int
    entity_type: EntityType
    left_text: str
    right_text: str
    signal_type: AliasSignalType
    confidence_score: float
    confidence_basis: str
    rationale: str
    left_occurrence_count: int
    right_occurrence_count: int
    shared_document_count: int
    left_context: str
    right_context: str
    latest_decision_id: int | None
    latest_revision: int | None
    latest_status: AliasDecisionStatus | None
    latest_reason: str | None
    latest_reviewer: str | None


@dataclass(frozen=True, slots=True)
class AliasReviewQueueReport:
    """Deterministic, read-only view of the open review queue."""

    open_count: int
    items: tuple[AliasReviewQueueItem, ...]


@dataclass(frozen=True, slots=True)
class RecordedAliasDecision:
    """Detached result of one committed manual decision."""

    decision_id: int
    proposal_id: int
    revision: int
    supersedes_decision_id: int | None
    status: AliasDecisionStatus
    reason: str
    reviewer: str


@dataclass(frozen=True, slots=True)
class _ReviewRow:
    proposal: AliasProposal
    left_candidate: EntityCandidate
    right_candidate: EntityCandidate
    latest_decision: AliasDecision | None


def get_alias_review_queue(
        *,
        limit: int = 20,
        session_factory: Callable[[], Session] = SessionLocal,
) -> AliasReviewQueueReport:
    """Return proposals with no final human decision, without writing state."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    with session_factory() as session:
        rows = _load_review_rows(session)

    open_rows = [
        row
        for row in rows
        if (
            row.latest_decision is None
            or row.latest_decision.status
            is AliasDecisionStatus.NEEDS_REVIEW
        )
    ]
    return AliasReviewQueueReport(
        open_count=len(open_rows),
        items=tuple(_queue_item(row) for row in open_rows[:limit]),
    )


def record_alias_decision(
        *,
        proposal_id: int,
        status: AliasDecisionStatus,
        reason: str,
        reviewer: str,
        session_factory: Callable[[], Session] = SessionLocal,
) -> RecordedAliasDecision:
    """Commit one explicit manual decision in one transaction."""

    decision = ManualAliasDecision(
        status=status,
        reason=reason,
        reviewer=reviewer,
    )
    with session_factory() as session:
        try:
            row = AliasDecisionService(session).decide(
                proposal_id=proposal_id,
                decision=decision,
            )
            result = RecordedAliasDecision(
                decision_id=row.id,
                proposal_id=row.alias_proposal_id,
                revision=row.revision,
                supersedes_decision_id=row.supersedes_alias_decision_id,
                status=row.status,
                reason=row.reason,
                reviewer=row.reviewer,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def _load_review_rows(session: Session) -> list[_ReviewRow]:
    left_candidate = aliased(EntityCandidate)
    right_candidate = aliased(EntityCandidate)
    statement = (
        select(AliasProposal, left_candidate, right_candidate)
        .join(
            left_candidate,
            left_candidate.id
            == AliasProposal.left_entity_candidate_id,
        )
        .join(
            right_candidate,
            right_candidate.id
            == AliasProposal.right_entity_candidate_id,
        )
        .order_by(AliasProposal.id.asc())
    )
    proposals = list(session.execute(statement))

    decisions = list(
        session.scalars(
            select(AliasDecision).order_by(
                AliasDecision.alias_proposal_id.asc(),
                AliasDecision.revision.asc(),
                AliasDecision.id.asc(),
            )
        )
    )
    latest_by_proposal: dict[int, AliasDecision] = {}
    for decision in decisions:
        latest_by_proposal[decision.alias_proposal_id] = decision

    return [
        _ReviewRow(
            proposal=proposal,
            left_candidate=left,
            right_candidate=right,
            latest_decision=latest_by_proposal.get(proposal.id),
        )
        for proposal, left, right in proposals
    ]


def _queue_item(row: _ReviewRow) -> AliasReviewQueueItem:
    latest = row.latest_decision
    return AliasReviewQueueItem(
        proposal_id=row.proposal.id,
        document_version_id=row.proposal.document_version_id,
        entity_type=row.proposal.entity_type,
        left_text=row.proposal.left_canonical_text,
        right_text=row.proposal.right_canonical_text,
        signal_type=row.proposal.signal_type,
        confidence_score=row.proposal.confidence_score,
        confidence_basis=row.proposal.confidence_basis,
        rationale=row.proposal.rationale,
        left_occurrence_count=row.proposal.left_occurrence_count,
        right_occurrence_count=row.proposal.right_occurrence_count,
        shared_document_count=row.proposal.shared_document_count,
        left_context=row.left_candidate.context_text,
        right_context=row.right_candidate.context_text,
        latest_decision_id=None if latest is None else latest.id,
        latest_revision=None if latest is None else latest.revision,
        latest_status=None if latest is None else latest.status,
        latest_reason=None if latest is None else latest.reason,
        latest_reviewer=None if latest is None else latest.reviewer,
    )
