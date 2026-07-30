from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from argus.logging.logger import configure_logging
from argus.services.acquisition_batch_runner import (
    AcquisitionBatchItemStatus,
    AcquisitionBatchReport,
)
from argus.services.article_acquisition_service import acquire_articles
from argus.services.acquisition_status_service import (
    get_acquisition_status,
)
from argus.services.collection_service import collect_articles
from argus.services.discourse_pipeline import run_discourse_pipeline
from argus.services.entity_mention_batch_runner import (
    EntityMentionBatchItemStatus,
    EntityMentionBatchReport,
)
from argus.services.entity_mention_pipeline import (
    run_entity_mention_pipeline,
)
from argus.services.entity_mention_audit_service import (
    get_entity_mention_audit,
)
from argus.services.entity_candidate_batch_runner import (
    EntityCandidateBatchItemStatus,
    EntityCandidateBatchReport,
)
from argus.services.entity_candidate_pipeline import (
    run_entity_candidate_pipeline,
)
from argus.services.entity_candidate_audit_service import (
    get_entity_candidate_audit,
)
from argus.services.alias_proposal_batch_runner import (
    AliasProposalBatchItemStatus,
    AliasProposalBatchReport,
)
from argus.services.alias_proposal_pipeline import (
    run_alias_proposal_pipeline,
)
from argus.services.alias_proposal_audit_service import (
    get_alias_proposal_audit,
)
from argus.services.alias_review_service import (
    get_alias_review_queue,
    record_alias_decision,
)
from argus.services.entity_resolution_service import (
    resolve_alias_identity,
)
from argus.services.entity_registry_audit_service import (
    get_entity_registry_audit,
)
from argus.services.safe_entity_projection_service import (
    get_safe_entity_projection,
)
from argus.services.document_entity_projection_service import (
    get_document_entity_projection,
)
from argus.services.document_entity_coverage_service import (
    get_document_entity_coverage,
)
from argus.services.latest_news_service import get_latest_news
from argus.services.telegram_bot_service import run_telegram_news_bot
from argus.knowledge import AliasDecisionStatus, EntityType
from argus.services.operational_pipeline_service import (
    run_operational_pipeline,
)
from argus.services.parsing_service import parse_articles
from argus.storage.migrations import upgrade_database

app = typer.Typer(
    name="argus",
    help="Explainable information-space analysis platform.",
    no_args_is_help=True,
)


def _echo_acquisition_report(report: AcquisitionBatchReport) -> None:
    typer.echo(
        f"total={report.total_count} "
        f"processed={report.processed_count} "
        f"retrieval_only={report.retrieval_only_count} "
        f"failed={report.failed_count}"
    )
    for item in report.items:
        if item.status is AcquisitionBatchItemStatus.PROCESSED:
            continue

        details = [
            f"candidate_id={item.candidate_id}",
            f"status={item.status.value}",
            (
                "stage="
                f"{item.failure_stage.value if item.failure_stage else 'unknown'}"
            ),
            f"url={item.url or 'unknown'}",
        ]
        if item.retrieval_outcome is not None:
            details.append(
                f"outcome={item.retrieval_outcome.value}"
            )
        if item.error_type is not None:
            details.append(f"error_type={item.error_type}")
        if item.error_message is not None:
            message = " ".join(item.error_message.split())
            details.append(f"error={message}")
        typer.echo(" ".join(details))


def _echo_entity_mention_report(
        report: EntityMentionBatchReport,
) -> None:
    typer.echo(
        f"total={report.total_count} "
        f"processed={report.processed_count} "
        f"failed={report.failed_count} "
        f"mentions={report.mention_count}"
    )
    for item in report.items:
        if item.status is EntityMentionBatchItemStatus.PROCESSED:
            continue

        details = [
            f"text_artifact_id={item.text_artifact_id}",
            f"status={item.status.value}",
        ]
        if item.error_type is not None:
            details.append(f"error_type={item.error_type}")
        if item.error_message is not None:
            message = " ".join(item.error_message.split())
            details.append(f"error={message}")
        typer.echo(" ".join(details))


def _echo_entity_candidate_report(
        report: EntityCandidateBatchReport,
) -> None:
    typer.echo(
        f"total={report.total_count} "
        f"processed={report.processed_count} "
        f"failed={report.failed_count} "
        f"candidates={report.candidate_count} "
        f"excluded={report.excluded_count}"
    )
    for item in report.items:
        if item.status is EntityCandidateBatchItemStatus.PROCESSED:
            continue

        details = [
            f"mention_artifact_id={item.mention_artifact_id}",
            f"status={item.status.value}",
        ]
        if item.error_type is not None:
            details.append(f"error_type={item.error_type}")
        if item.error_message is not None:
            message = " ".join(item.error_message.split())
            details.append(f"error={message}")
        typer.echo(" ".join(details))


def _echo_alias_proposal_report(
        report: AliasProposalBatchReport,
) -> None:
    typer.echo(
        f"total={report.total_count} "
        f"processed={report.processed_count} "
        f"failed={report.failed_count} "
        f"proposals={report.proposal_count}"
    )
    for item in report.items:
        if item.status is AliasProposalBatchItemStatus.PROCESSED:
            continue

        details = [
            f"candidate_artifact_id={item.candidate_artifact_id}",
            f"status={item.status.value}",
        ]
        if item.error_type is not None:
            details.append(f"error_type={item.error_type}")
        if item.error_message is not None:
            message = " ".join(item.error_message.split())
            details.append(f"error={message}")
        typer.echo(" ".join(details))


@app.callback()
def initialize_database() -> None:
    """Apply pending database migrations before running a command."""

    upgrade_database()
    configure_logging()

@app.command()
def collect() -> None:
    """Collect new article metadata from configured sources."""

    collect_articles()


@app.command()
def acquire(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of discovered articles to acquire.",
        ),
        retry_unsuccessful: bool = typer.Option(
            False,
            "--retry-unsuccessful",
            help="Retry candidates with no successful retrieval.",
        ),
) -> None:
    """Retrieve and normalize stored acquisition candidates."""

    report = acquire_articles(
        limit=limit,
        retry_unsuccessful=retry_unsuccessful,
    )
    _echo_acquisition_report(report)


@app.command()
def acquisition_status() -> None:
    """Report the current state of the acquisition queue."""

    report = get_acquisition_status()
    typer.echo(
        f"total={report.total} "
        f"unattempted={report.unattempted} "
        f"succeeded={report.succeeded} "
        f"retryable={report.retryable} "
        f"access_restricted={report.access_restricted} "
        f"exhausted={report.exhausted}"
    )
    for source in report.paused_sources:
        typer.echo(f"paused_source={source}")


@app.command()
def parse(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of article bodies to extract.",
        ),
        retry_failed: bool = typer.Option(
            False,
            "--retry-failed",
            help="Retry articles whose previous parsing attempt failed.",
        ),
        newest: bool = typer.Option(
            False,
            "--newest",
            help=(
                "Parse the newest published articles first instead of "
                "the oldest pending articles."
            ),
        ),
) -> None:
    """Extract full text for legacy articles."""

    parse_articles(
        limit=limit,
        retry_failed=retry_failed,
        newest_first=newest,
    )


@app.command()
def latest_news(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of recent collected articles to show.",
        ),
        excerpt_chars: int = typer.Option(
            240,
            "--excerpt-chars",
            min=40,
            max=1000,
            help="Maximum length of each normalized text excerpt.",
        ),
        timezone_name: str = typer.Option(
            "UTC",
            "--timezone",
            help=(
                "IANA timezone used for publication times, for example "
                "Europe/Amsterdam."
            ),
        ),
        details: bool = typer.Option(
            False,
            "--details",
            help="Include article IDs, parsing state, and fetch metadata.",
        ),
) -> None:
    """Show a read-only chronological feed of collected articles."""

    report = get_latest_news(
        limit=limit,
        excerpt_chars=excerpt_chars,
    )
    try:
        output_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise typer.BadParameter(
            f"Unknown IANA timezone: {timezone_name}",
            param_hint="--timezone",
        ) from error

    typer.echo(
        f"Latest collected news — shown={len(report.items)} "
        f"content={report.content_count} "
        f"summary={report.summary_count} "
        f"headline_only={report.headline_only_count} "
        f"timezone={timezone_name}"
    )
    for position, item in enumerate(report.items, start=1):
        published_at = _format_news_datetime(
            item.published_at,
            output_timezone,
        )
        typer.echo("")
        typer.echo(f"{position}. [{published_at}] {item.source}")
        typer.echo(f"   {item.title}")
        if item.excerpt is not None:
            typer.echo(f"   {item.excerpt}")
        typer.echo(f"   url={item.url}")
        if details:
            fetched_at = _format_news_datetime(
                item.fetched_at,
                output_timezone,
            )
            typer.echo(
                f"   details: article_id={item.article_id} "
                f"fetched_at={fetched_at} "
                f"language={item.language or 'unknown'} "
                f"parsing={item.parsing_status} "
                f"excerpt_source={item.excerpt_source or 'unavailable'}"
            )


def _format_news_datetime(
        value: datetime | None,
        output_timezone: ZoneInfo,
) -> str:
    if value is None:
        return "time unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    localized = value.astimezone(output_timezone)
    return localized.strftime("%Y-%m-%d %H:%M %Z")


@app.command()
def telegram_bot(
        limit: int = typer.Option(
            10,
            min=1,
            max=50,
            help="Maximum number of recent articles sent by /latest.",
        ),
        excerpt_chars: int = typer.Option(
            500,
            "--excerpt-chars",
            min=40,
            max=1000,
            help="Maximum length of each normalized text excerpt.",
        ),
        timezone_name: str = typer.Option(
            "UTC",
            "--timezone",
            help="IANA timezone used for publication times.",
        ),
        poll_timeout: int = typer.Option(
            30,
            "--poll-timeout",
            min=1,
            max=50,
            help="Telegram long-poll timeout in seconds.",
        ),
        once: bool = typer.Option(
            False,
            "--once",
            help="Process one Telegram update batch and exit.",
        ),
        auto_delivery: bool = typer.Option(
            False,
            "--auto-delivery",
            help="Collect, parse and deliver newly ingested articles.",
        ),
        delivery_interval_minutes: int = typer.Option(
            60,
            "--delivery-interval-minutes",
            min=1,
            help="Minutes between automatic collection cycles.",
        ),
        delivery_limit: int = typer.Option(
            20,
            "--delivery-limit",
            min=1,
            max=100,
            help="Maximum unseen articles loaded per delivery batch.",
        ),
        parse_limit: int = typer.Option(
            20,
            "--auto-parse-limit",
            min=1,
            max=100,
            help="Maximum undelivered articles parsed per automatic cycle.",
        ),
        latest_cooldown_seconds: int = typer.Option(
            10,
            "--latest-cooldown-seconds",
            min=0,
            max=3600,
            help="Per-chat cooldown between /latest requests.",
        ),
        delivery_state_path: Path = typer.Option(
            Path("data/telegram_delivery_state.json"),
            "--delivery-state-path",
            help=(
                "Legacy single-user cursor imported for the administrator."
            ),
        ),
) -> None:
    """Run the public multi-user Telegram reader bot."""

    try:
        output_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise typer.BadParameter(
            f"Unknown IANA timezone: {timezone_name}",
            param_hint="--timezone",
        ) from error

    try:
        run_telegram_news_bot(
            news_limit=limit,
            excerpt_chars=excerpt_chars,
            output_timezone=output_timezone,
            poll_timeout_seconds=poll_timeout,
            run_once=once,
            automatic_delivery=auto_delivery,
            automatic_interval_seconds=(
                delivery_interval_minutes * 60
            ),
            automatic_delivery_limit=delivery_limit,
            automatic_parse_limit=parse_limit,
            latest_cooldown_seconds=latest_cooldown_seconds,
            delivery_state_path=delivery_state_path,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command()
def analyze(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of articles to analyze.",
        ),
        retry_failed: bool = typer.Option(
            False,
            "--retry-failed",
            help="Retry articles whose previous analysis attempt failed.",
        ),
) -> None:
    """Run discourse analysis on stored articles."""

    run_discourse_pipeline(
        limit=limit,
        retry_failed=retry_failed,
    )


@app.command()
def extract_mentions(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of text artifacts to process.",
        ),
) -> None:
    """Extract versioned entity mentions from pending text artifacts."""

    report = run_entity_mention_pipeline(limit=limit)
    _echo_entity_mention_report(report)


@app.command()
def generate_candidates(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of entity-mention artifacts to process.",
        ),
) -> None:
    """Generate versioned entity candidates from pending NER artifacts."""

    report = run_entity_candidate_pipeline(limit=limit)
    _echo_entity_candidate_report(report)


@app.command()
def propose_aliases(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of entity-candidate artifacts to process.",
        ),
) -> None:
    """Generate versioned, review-only alias proposals."""

    report = run_alias_proposal_pipeline(limit=limit)
    _echo_alias_proposal_report(report)


@app.command()
def alias_proposal_audit(
        top: int = typer.Option(
            10,
            min=1,
            help="Number of proposal runs to show.",
        ),
        examples: int = typer.Option(
            20,
            min=1,
            help="Number of evidence-bearing proposals to show.",
        ),
) -> None:
    """Audit persisted alias proposals without changing data."""

    report = get_alias_proposal_audit(
        top=top,
        examples=examples,
    )
    typer.echo(
        f"proposals={report.proposal_count} "
        f"artifacts={report.artifact_count} "
        f"document_versions={report.document_version_count}"
    )
    for item in report.counts_by_signal:
        typer.echo(f"signal={item.name} proposals={item.count}")
    for item in report.counts_by_type:
        typer.echo(f"type={item.name} proposals={item.count}")
    for item in report.counts_by_confidence_band:
        typer.echo(
            f"confidence_band={item.name} proposals={item.count}"
        )
    for item in report.runs:
        title = _single_line(item.title or "untitled")
        typer.echo(
            f"run artifact_id={item.artifact_id} "
            f"input_artifact_id={item.input_artifact_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"proposals={item.proposal_count} "
            f"proposer={item.proposer_version} "
            f"title={title!r}"
        )
    for item in report.examples:
        left_context = _single_line(item.left_context)
        right_context = _single_line(item.right_context)
        title = _single_line(item.title or "untitled")
        typer.echo(
            f"proposal proposal_id={item.proposal_id} "
            f"artifact_id={item.artifact_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"type={item.entity_type.value} "
            f"left={item.left_text!r} right={item.right_text!r} "
            f"signal={item.signal_type.value} "
            f"confidence={item.confidence_score:.2f} "
            f"band={item.confidence_band} "
            f"basis={item.confidence_basis!r} "
            f"left_count={item.left_occurrence_count} "
            f"right_count={item.right_occurrence_count} "
            f"shared_documents={item.shared_document_count} "
            f"rationale={item.rationale!r} "
            f"left_context={left_context!r} "
            f"right_context={right_context!r} "
            f"title={title!r}"
        )
    for limitation in report.quality_limitations:
        typer.echo(f"limitation={_single_line(limitation)!r}")


@app.command()
def alias_review_queue(
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of open alias proposals to show.",
        ),
) -> None:
    """Show proposals awaiting a final human decision."""

    report = get_alias_review_queue(limit=limit)
    typer.echo(
        f"open={report.open_count} shown={len(report.items)}"
    )
    for item in report.items:
        left_context = _single_line(item.left_context)
        right_context = _single_line(item.right_context)
        latest_status = (
            "unreviewed"
            if item.latest_status is None
            else item.latest_status.value
        )
        typer.echo(
            f"proposal proposal_id={item.proposal_id} "
            f"document_version_id={item.document_version_id} "
            f"type={item.entity_type.value} "
            f"left={item.left_text!r} right={item.right_text!r} "
            f"signal={item.signal_type.value} "
            f"confidence={item.confidence_score:.2f} "
            f"basis={item.confidence_basis!r} "
            f"left_count={item.left_occurrence_count} "
            f"right_count={item.right_occurrence_count} "
            f"shared_documents={item.shared_document_count} "
            f"rationale={item.rationale!r} "
            f"latest_status={latest_status} "
            f"latest_revision={item.latest_revision or 0} "
            f"latest_reason={item.latest_reason!r} "
            f"latest_reviewer={item.latest_reviewer!r} "
            f"left_context={left_context!r} "
            f"right_context={right_context!r}"
        )


@app.command()
def decide_alias(
        proposal_id: int = typer.Option(
            ...,
            "--proposal-id",
            min=1,
            help="Exact alias proposal identifier to review.",
        ),
        status: AliasDecisionStatus = typer.Option(
            ...,
            "--status",
            case_sensitive=False,
            help="Manual verdict: approved, rejected or needs_review.",
        ),
        reason: str = typer.Option(
            ...,
            "--reason",
            help="Evidence-based reason for the verdict.",
        ),
        reviewer: str = typer.Option(
            ...,
            "--reviewer",
            help="Identifier of the human reviewer.",
        ),
) -> None:
    """Append one explicit human decision for one exact proposal."""

    result = record_alias_decision(
        proposal_id=proposal_id,
        status=status,
        reason=reason,
        reviewer=reviewer,
    )
    typer.echo(
        f"decision_id={result.decision_id} "
        f"proposal_id={result.proposal_id} "
        f"revision={result.revision} "
        f"supersedes={result.supersedes_decision_id} "
        f"status={result.status.value} "
        f"reviewer={result.reviewer!r} "
        f"reason={result.reason!r}"
    )


@app.command()
def resolve_alias(
        proposal_id: int = typer.Option(
            ...,
            "--proposal-id",
            min=1,
            help="Approved alias proposal to consume.",
        ),
        entity_id: int | None = typer.Option(
            None,
            "--entity-id",
            min=1,
            help="Existing entity to extend; inferred when possible.",
        ),
        canonical_candidate_id: int | None = typer.Option(
            None,
            "--canonical-candidate-id",
            min=1,
            help=(
                "Proposal candidate chosen as the canonical name when "
                "creating a new entity."
            ),
        ),
) -> None:
    """Create or extend one entity from an explicit approved decision."""

    result = resolve_alias_identity(
        proposal_id=proposal_id,
        entity_id=entity_id,
        canonical_candidate_id=canonical_candidate_id,
    )
    candidate_ids = ",".join(
        str(item) for item in result.assigned_candidate_ids
    )
    typer.echo(
        f"entity_id={result.entity_id} "
        f"created={str(result.entity_created).lower()} "
        f"type={result.entity_type} "
        f"canonical_name={result.canonical_name!r} "
        f"canonical_candidate_id="
        f"{result.canonical_entity_candidate_id} "
        f"alias_decision_id={result.alias_decision_id} "
        f"assigned_candidate_ids={candidate_ids}"
    )


@app.command()
def entity_registry_audit(
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of entity/proposal links to show.",
        ),
) -> None:
    """Audit current entity-registry validity without changing data."""

    report = get_entity_registry_audit(limit=limit)
    typer.echo(
        f"entities={report.entity_count} "
        f"safe={report.safe_entity_count} "
        f"blocked={report.blocked_entity_count} "
        f"links={report.link_count} "
        f"shown={len(report.items)}"
    )
    for count in report.counts_by_validity:
        typer.echo(
            f"validity={count.validity.value} links={count.count}"
        )
    for item in report.items:
        applied = ",".join(
            str(decision_id)
            for decision_id in item.applied_decision_ids
        )
        typer.echo(
            f"entity entity_id={item.entity_id} "
            f"type={item.entity_type.value} "
            f"canonical_name={item.canonical_name!r} "
            f"safe_for_downstream="
            f"{str(item.safe_for_downstream_use).lower()} "
            f"proposal_id={item.proposal_id} "
            f"candidate_ids="
            f"{item.left_candidate_id},{item.right_candidate_id} "
            f"applied_decision_ids={applied or 'none'} "
            f"latest_decision_id={item.latest_decision_id} "
            f"latest_revision={item.latest_revision} "
            f"latest_status={item.latest_status.value} "
            f"validity={item.validity.value}"
        )


@app.command()
def safe_entities(
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of safe entities to show.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type filter.",
        ),
) -> None:
    """Expose only fully active entities for downstream consumers."""

    projection = get_safe_entity_projection(
        limit=limit,
        entity_type=entity_type,
    )
    typer.echo(
        f"safe_entities={projection.safe_entity_count} "
        f"shown={len(projection.items)}"
    )
    for entity in projection.items:
        candidate_ids = ",".join(
            str(candidate.entity_candidate_id)
            for candidate in entity.candidates
        )
        decision_ids = ",".join(
            str(link.latest_alias_decision_id)
            for link in entity.active_resolutions
        )
        typer.echo(
            f"entity entity_id={entity.entity_id} "
            f"type={entity.entity_type.value} "
            f"canonical_name={entity.canonical_name!r} "
            f"canonical_candidate_id="
            f"{entity.canonical_entity_candidate_id} "
            f"candidate_ids={candidate_ids} "
            f"active_decision_ids={decision_ids}"
        )


@app.command()
def document_entities(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version whose resolved entities are shown.",
        ),
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of resolved entities to show.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type filter.",
        ),
) -> None:
    """Expose safe resolved identities observed in one document version."""

    projection = get_document_entity_projection(
        document_version_id=document_version_id,
        limit=limit,
        entity_type=entity_type,
    )
    typer.echo(
        f"document_version_id={projection.document_version_id} "
        f"document_id={projection.document_id} "
        f"version={projection.version_number} "
        f"resolved_entities={projection.resolved_entity_count} "
        f"resolved_occurrences={projection.resolved_occurrence_count} "
        f"shown={len(projection.items)}"
    )
    for entity in projection.items:
        decision_ids = ",".join(
            str(link.latest_alias_decision_id)
            for link in entity.active_resolutions
        )
        typer.echo(
            f"entity entity_id={entity.entity_id} "
            f"type={entity.entity_type.value} "
            f"canonical_name={entity.canonical_name!r} "
            f"canonical_candidate_id="
            f"{entity.canonical_entity_candidate_id} "
            f"occurrences={len(entity.occurrences)} "
            f"active_decision_ids={decision_ids}"
        )
        for occurrence in entity.occurrences:
            typer.echo(
                f"occurrence entity_candidate_id="
                f"{occurrence.entity_candidate_id} "
                f"entity_mention_id={occurrence.entity_mention_id} "
                f"derived_artifact_id={occurrence.derived_artifact_id} "
                f"span={occurrence.start_char}:{occurrence.end_char} "
                f"surface={occurrence.surface_text!r} "
                f"canonical={occurrence.canonical_text!r} "
                f"assignment_decision_id="
                f"{occurrence.assigned_by_alias_decision_id}"
            )


@app.command()
def document_entity_coverage(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version whose entity coverage is audited.",
        ),
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of candidate evidence rows to show.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type filter applied before counts.",
        ),
) -> None:
    """Audit candidate resolution coverage in one document version."""

    report = get_document_entity_coverage(
        document_version_id=document_version_id,
        limit=limit,
        entity_type=entity_type,
    )
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"document_id={report.document_id} "
        f"version={report.version_number} "
        f"candidates={report.candidate_count} "
        f"shown={len(report.items)}"
    )
    for count in report.counts_by_status:
        typer.echo(
            f"coverage={count.status.value} candidates={count.count}"
        )
    for item in report.items:
        blocking = ",".join(
            validity.value for validity in item.blocking_validities
        )
        span = (
            "unknown"
            if item.start_char is None or item.end_char is None
            else f"{item.start_char}:{item.end_char}"
        )
        typer.echo(
            f"candidate entity_candidate_id="
            f"{item.entity_candidate_id} "
            f"entity_mention_id={item.entity_mention_id} "
            f"derived_artifact_id={item.derived_artifact_id} "
            f"type={item.entity_type.value} "
            f"coverage={item.status.value} "
            f"entity_id={item.entity_id or 'none'} "
            f"assignment_decision_id="
            f"{item.assigned_by_alias_decision_id or 'none'} "
            f"blocking_validities={blocking or 'none'} "
            f"span={span} "
            f"surface={item.surface_text!r} "
            f"canonical={item.canonical_text!r} "
            f"provenance_issue={item.provenance_issue!r}"
        )


@app.command()
def candidate_audit(
        top: int = typer.Option(
            10,
            min=1,
            help="Number of frequent forms and dense runs to show.",
        ),
        examples: int = typer.Option(
            10,
            min=1,
            help="Number of deterministic review examples to show.",
        ),
        pairs: int = typer.Option(
            10,
            min=1,
            help="Number of review-only potential alias pairs to show.",
        ),
) -> None:
    """Audit persisted entity candidates without changing data."""

    report = get_entity_candidate_audit(
        top=top,
        examples=examples,
        pairs=pairs,
    )
    typer.echo(
        f"candidates={report.candidate_count} "
        f"artifacts={report.artifact_count} "
        f"document_versions={report.document_version_count}"
    )
    for item in report.counts_by_language:
        typer.echo(f"language={item.name} candidates={item.count}")
    for item in report.counts_by_type:
        typer.echo(f"type={item.name} candidates={item.count}")
    for item in report.frequent_candidates:
        variants = " | ".join(item.surface_variants)
        typer.echo(
            f"frequent type={item.entity_type.value} "
            f"canonical={item.canonical_text!r} "
            f"candidates={item.candidate_count} "
            f"document_versions={item.document_count} "
            f"surfaces={variants!r}"
        )
    for item in report.densest_runs:
        title = _single_line(item.title or "untitled")
        typer.echo(
            f"dense artifact_id={item.artifact_id} "
            f"input_artifact_id={item.input_artifact_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"candidates={item.candidate_count} "
            f"unique_forms={item.unique_form_count} "
            f"canonicalizer={item.method_version} "
            f"title={title!r}"
        )
    for item in report.alias_signals:
        left_context = _single_line(item.left_context)
        right_context = _single_line(item.right_context)
        typer.echo(
            f"alias-signal type={item.entity_type.value} "
            f"left={item.left_text!r} right={item.right_text!r} "
            f"reason={item.reason} "
            f"left_count={item.left_count} "
            f"right_count={item.right_count} "
            f"shared_documents={item.shared_document_count} "
            f"left_context={left_context!r} "
            f"right_context={right_context!r}"
        )
    for item in report.examples:
        surface = _single_line(item.surface_text)
        canonical = _single_line(item.canonical_text)
        context = _single_line(item.context_text)
        typer.echo(
            f"example candidate_id={item.candidate_id} "
            f"artifact_id={item.artifact_id} "
            f"mention_id={item.mention_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"type={item.entity_type.value} "
            f"context_span={item.context_start_char}:"
            f"{item.context_end_char} "
            f"surface={surface!r} canonical={canonical!r} "
            f"context={context!r}"
        )


@app.command()
def mention_audit(
        top: int = typer.Option(
            10,
            min=1,
            help="Number of frequent forms and dense runs to show.",
        ),
        examples: int = typer.Option(
            10,
            min=1,
            help="Number of deterministic review examples to show.",
        ),
) -> None:
    """Audit persisted entity mentions without changing data."""

    report = get_entity_mention_audit(
        top=top,
        examples=examples,
    )
    typer.echo(
        f"mentions={report.mention_count} "
        f"artifacts={report.artifact_count} "
        f"document_versions={report.document_version_count}"
    )
    for item in report.counts_by_language:
        typer.echo(f"language={item.name} mentions={item.count}")
    for item in report.counts_by_type:
        typer.echo(f"type={item.name} mentions={item.count}")
    for item in report.frequent_mentions:
        variants = " | ".join(item.surface_variants)
        typer.echo(
            f"frequent type={item.entity_type.value} "
            f"normalized={item.normalized_text!r} "
            f"mentions={item.mention_count} "
            f"document_versions={item.document_count} "
            f"surfaces={variants!r}"
        )
    for item in report.densest_runs:
        title = _single_line(item.title or "untitled")
        typer.echo(
            f"dense artifact_id={item.artifact_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"mentions={item.mention_count} "
            f"unique_forms={item.unique_form_count} "
            f"model={item.method_version} "
            f"title={title!r}"
        )
    for item in report.examples:
        surface = _single_line(item.surface_text)
        normalized = _single_line(item.normalized_text)
        typer.echo(
            f"example mention_id={item.mention_id} "
            f"artifact_id={item.artifact_id} "
            f"document_version_id={item.document_version_id} "
            f"language={item.language} "
            f"type={item.entity_type.value} "
            f"label={item.source_label} "
            f"span={item.start_char}:{item.end_char} "
            f"surface={surface!r} normalized={normalized!r}"
        )


def _single_line(value: str) -> str:
    return " ".join(value.split())


@app.command()
def run(
        acquisition_limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of discovered articles to acquire.",
        ),
        analysis_limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of articles to analyze.",
        ),
        retry_unsuccessful: bool = typer.Option(
            False,
            "--retry-unsuccessful",
            help="Retry candidates with no successful retrieval.",
        ),
        retry_failed: bool = typer.Option(
            False,
            "--retry-failed",
            help="Retry failed analysis operations.",
        ),
) -> None:
    """Run discovery, acquisition and discourse analysis."""

    report = run_operational_pipeline(
        acquisition_limit=acquisition_limit,
        analysis_limit=analysis_limit,
        retry_unsuccessful=retry_unsuccessful,
        retry_failed_analysis=retry_failed,
    )
    _echo_acquisition_report(report.acquisition)


if __name__ == "__main__":
    app()
