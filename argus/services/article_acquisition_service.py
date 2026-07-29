from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from argus.acquisition import RetrievalOutcome
from argus.collector.rss_connector import (
    RSS_CONNECTOR_ID,
    RSSConnector,
)
from argus.config import (
    RAW_ARTIFACT_DIRECTORY,
    RSS_FEEDS,
    RSSFeedConfig,
)
from argus.database import SessionLocal
from argus.documents import DocumentType
from argus.extractors.trafilatura_extractor import (
    TrafilaturaTextExtractor,
)
from argus.logging.logger import get_logger
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
    RetrievalAttempt,
    Source,
)
from argus.services.acquisition_batch_runner import (
    AcquisitionBatchItem,
    AcquisitionBatchReport,
    AcquisitionBatchRunner,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore


logger = get_logger(__name__)

MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS = 3
SOURCE_ACCESS_RESTRICTION_THRESHOLD = 3
SOURCE_ACCESS_RESTRICTION_COOLDOWN = timedelta(hours=24)


def acquire_articles(
        *,
        limit: int = 20,
        retry_unsuccessful: bool = False,
) -> AcquisitionBatchReport:
    """Retrieve and normalize a bounded batch of persisted RSS candidates."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    feeds_by_endpoint = {
        feed.effective_endpoint_identifier: feed
        for feed in RSS_FEEDS
    }
    items = _pending_article_items(
        limit=limit,
        retry_unsuccessful=retry_unsuccessful,
        feeds_by_endpoint=feeds_by_endpoint,
    )
    runner = AcquisitionBatchRunner(
        SessionLocal,
        artifact_store=FileSystemRawArtifactStore(
            RAW_ARTIFACT_DIRECTORY
        ),
        extractor=TrafilaturaTextExtractor(),
    )
    report = runner.run(items)

    logger.info(
        "Acquisition finished; total: %s; processed: %s; "
        "retrieval only: %s; failed: %s",
        report.total_count,
        report.processed_count,
        report.retrieval_only_count,
        report.failed_count,
    )
    return report


def _pending_article_items(
        *,
        limit: int,
        retry_unsuccessful: bool,
        feeds_by_endpoint: dict[str, RSSFeedConfig],
        selection_time: datetime | None = None,
) -> tuple[AcquisitionBatchItem, ...]:
    successful_attempt_exists = exists(
        select(RetrievalAttempt.id).where(
            RetrievalAttempt.candidate_id
            == AcquisitionCandidate.id,
            RetrievalAttempt.outcome
            == RetrievalOutcome.SUCCEEDED,
        )
    )
    any_attempt_exists = exists(
        select(RetrievalAttempt.id).where(
            RetrievalAttempt.candidate_id
            == AcquisitionCandidate.id,
        )
    )
    access_restricted_attempt_exists = exists(
        select(RetrievalAttempt.id).where(
            RetrievalAttempt.candidate_id
            == AcquisitionCandidate.id,
            RetrievalAttempt.outcome
            == RetrievalOutcome.ACCESS_RESTRICTED,
        )
    )
    attempt_count = (
        select(func.count(RetrievalAttempt.id))
        .where(
            RetrievalAttempt.candidate_id
            == AcquisitionCandidate.id,
        )
        .correlate(AcquisitionCandidate)
        .scalar_subquery()
    )
    attempted_filter = (
        and_(
            ~successful_attempt_exists,
            ~access_restricted_attempt_exists,
            attempt_count < MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS,
        )
        if retry_unsuccessful
        else ~any_attempt_exists
    )
    with SessionLocal() as session:
        paused_sources = _temporarily_paused_sources(
            session=session,
            selection_time=(
                selection_time
                if selection_time is not None
                else datetime.now(timezone.utc)
            ),
        )
        source_filter = (
            CollectionEndpoint.source_id.not_in(paused_sources)
            if paused_sources
            else True
        )
        statement = (
            select(AcquisitionCandidate, CollectionEndpoint)
            .join(
                CollectionEndpoint,
                CollectionEndpoint.id
                == AcquisitionCandidate.endpoint_id,
            )
            .where(
                AcquisitionCandidate.article_id.is_not(None),
                CollectionEndpoint.is_active.is_(True),
                CollectionEndpoint.connector_id == RSS_CONNECTOR_ID,
                attempted_filter,
                source_filter,
            )
            .order_by(
                AcquisitionCandidate.first_discovered_at,
                AcquisitionCandidate.id,
            )
            .limit(limit)
        )
        rows = session.execute(statement).all()

    if paused_sources:
        logger.info(
            "Acquisition temporarily paused for access-restricted "
            "sources: %s",
            ", ".join(sorted(paused_sources.values())),
        )

    items = []
    for candidate, endpoint in rows:
        feed = feeds_by_endpoint.get(endpoint.identifier)
        if feed is None:
            logger.warning(
                "No RSS configuration for endpoint %s; "
                "candidate %s skipped.",
                endpoint.identifier,
                candidate.id,
            )
            continue

        items.append(
            AcquisitionBatchItem(
                endpoint_id=endpoint.id,
                candidate_id=candidate.id,
                connector=RSSConnector(feed),
                document_type=DocumentType.ARTICLE,
                request_metadata={
                    "trigger": "cli",
                    "command": "acquire",
                },
            )
        )

    return tuple(items)


def _temporarily_paused_sources(
        *,
        session: Session,
        selection_time: datetime,
) -> dict[int, str]:
    cutoff = selection_time - SOURCE_ACCESS_RESTRICTION_COOLDOWN
    statement = (
        select(
            Source.id,
            Source.identifier,
            RetrievalAttempt.outcome,
        )
        .join(
            CollectionEndpoint,
            CollectionEndpoint.source_id == Source.id,
        )
        .join(
            RetrievalAttempt,
            RetrievalAttempt.endpoint_id == CollectionEndpoint.id,
        )
        .where(RetrievalAttempt.retrieved_at >= cutoff)
        .order_by(
            Source.id,
            RetrievalAttempt.retrieved_at.desc(),
            RetrievalAttempt.id.desc(),
        )
    )
    rows = session.execute(statement).all()
    recent_outcomes: dict[int, list[RetrievalOutcome]] = {}
    source_identifiers: dict[int, str] = {}

    for source_id, source_identifier, outcome in rows:
        outcomes = recent_outcomes.setdefault(source_id, [])
        if len(outcomes) >= SOURCE_ACCESS_RESTRICTION_THRESHOLD:
            continue
        outcomes.append(outcome)
        source_identifiers[source_id] = source_identifier

    return {
        source_id: source_identifiers[source_id]
        for source_id, outcomes in recent_outcomes.items()
        if (
            len(outcomes) == SOURCE_ACCESS_RESTRICTION_THRESHOLD
            and all(
                outcome is RetrievalOutcome.ACCESS_RESTRICTED
                for outcome in outcomes
            )
        )
    }
