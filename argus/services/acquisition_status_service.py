from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, func, select

from argus.acquisition import RetrievalOutcome
from argus.collector.rss_connector import RSS_CONNECTOR_ID
from argus.database import SessionLocal
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
    RetrievalAttempt,
)
from argus.services.article_acquisition_service import (
    MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS,
    _temporarily_paused_sources,
)


@dataclass(frozen=True, slots=True)
class AcquisitionStatusReport:
    total: int
    unattempted: int
    succeeded: int
    retryable: int
    access_restricted: int
    exhausted: int
    paused_sources: tuple[str, ...]


def get_acquisition_status(
        *,
        status_time: datetime | None = None,
) -> AcquisitionStatusReport:
    """Summarize the mutually exclusive states of active RSS candidates."""

    current_time = (
        status_time
        if status_time is not None
        else datetime.now(timezone.utc)
    )
    attempt_summary = (
        select(
            RetrievalAttempt.candidate_id.label("candidate_id"),
            func.count(RetrievalAttempt.id).label("attempt_count"),
            func.sum(
                case(
                    (
                        RetrievalAttempt.outcome
                        == RetrievalOutcome.SUCCEEDED,
                        1,
                    ),
                    else_=0,
                )
            ).label("succeeded_count"),
            func.sum(
                case(
                    (
                        RetrievalAttempt.outcome
                        == RetrievalOutcome.ACCESS_RESTRICTED,
                        1,
                    ),
                    else_=0,
                )
            ).label("access_restricted_count"),
        )
        .where(RetrievalAttempt.candidate_id.is_not(None))
        .group_by(RetrievalAttempt.candidate_id)
        .subquery()
    )
    attempt_count = func.coalesce(
        attempt_summary.c.attempt_count,
        0,
    )
    succeeded_count = func.coalesce(
        attempt_summary.c.succeeded_count,
        0,
    )
    access_restricted_count = func.coalesce(
        attempt_summary.c.access_restricted_count,
        0,
    )
    unattempted_condition = attempt_count == 0
    succeeded_condition = succeeded_count > 0
    access_restricted_condition = (
        (succeeded_count == 0)
        & (access_restricted_count > 0)
    )
    retryable_condition = (
        (succeeded_count == 0)
        & (access_restricted_count == 0)
        & (attempt_count > 0)
        & (attempt_count < MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS)
    )
    exhausted_condition = (
        (succeeded_count == 0)
        & (access_restricted_count == 0)
        & (attempt_count >= MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS)
    )
    statement = (
        select(
            func.count(AcquisitionCandidate.id),
            func.sum(case((unattempted_condition, 1), else_=0)),
            func.sum(case((succeeded_condition, 1), else_=0)),
            func.sum(case((retryable_condition, 1), else_=0)),
            func.sum(
                case((access_restricted_condition, 1), else_=0)
            ),
            func.sum(case((exhausted_condition, 1), else_=0)),
        )
        .join(
            CollectionEndpoint,
            CollectionEndpoint.id
            == AcquisitionCandidate.endpoint_id,
        )
        .outerjoin(
            attempt_summary,
            attempt_summary.c.candidate_id
            == AcquisitionCandidate.id,
        )
        .where(
            AcquisitionCandidate.article_id.is_not(None),
            CollectionEndpoint.is_active.is_(True),
            CollectionEndpoint.connector_id == RSS_CONNECTOR_ID,
        )
    )

    with SessionLocal() as session:
        counts = session.execute(statement).one()
        paused_sources = _temporarily_paused_sources(
            session=session,
            selection_time=current_time,
        )

    values = tuple(int(value or 0) for value in counts)
    return AcquisitionStatusReport(
        total=values[0],
        unattempted=values[1],
        succeeded=values[2],
        retryable=values[3],
        access_restricted=values[4],
        exhausted=values[5],
        paused_sources=tuple(sorted(paused_sources.values())),
    )
