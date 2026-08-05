from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from argus.analysis.calibration import (
    calibrate_threshold,
    corpus_summary,
    evaluate_test_split,
    load_calibration_corpus,
    write_canonical_json,
)
from argus.analysis.corpus_builder import (
    build_corpus_from_manifest,
    verify_corpus_build,
    write_corpus_build,
)
from argus.analysis.corpus_intake import (
    assemble_source_manifest,
    inspect_source_intake,
    register_human_source,
    register_synthetic_source,
)
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
from argus.services.candidate_resolution_service import (
    resolve_candidate_identity,
)
from argus.services.candidate_resolution_queue_service import (
    get_candidate_resolution_queue,
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
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessStatus,
    get_document_entity_readiness,
)
from argus.services.corpus_entity_readiness_service import (
    get_corpus_entity_readiness,
)
from argus.services.ready_document_selector_service import (
    select_ready_document_versions,
)
from argus.services.document_analysis_input_service import (
    get_document_analysis_input,
)
from argus.services.document_pair_event_similarity_service import (
    EventSimilarityConfiguration,
    get_document_pair_event_similarity,
)
from argus.services.event_fragment_service import get_event_fragments
from argus.services.event_fragment_segmentation_service import (
    inspect_document_text,
    segment_event_fragments,
)
from argus.services.transcript_ingestion_service import ingest_transcript_file
from argus.services.transcript_timeline_service import (
    inspect_transcript_timeline,
)
from argus.services.youtube_transcript_ingestion_service import (
    ingest_youtube_transcript,
)
from argus.transcript_sources.youtube import YouTubeTranscriptSource
from argus.transcripts import TranscriptFormat, TranscriptKind
from argus.services.analysis_run_service import prepare_analysis_run
from argus.services.software_provenance_service import (
    resolve_software_provenance,
)
from argus.services.analysis_execution_service import (
    execute_analysis_run,
    get_analysis_evidence,
    get_analysis_attempt_history,
    get_analysis_run_result,
    recover_stale_analysis_run,
)
from argus.services.latest_news_service import get_latest_news
from argus.services.telegram_bot_service import run_telegram_news_bot
from argus.knowledge import (
    AliasDecisionStatus,
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
    ManualCandidateResolutionDecision,
)
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
def resolve_candidate(
        candidate_id: int = typer.Option(
            ...,
            "--candidate-id",
            min=1,
            help="Seed entity candidate for the explicit decision.",
        ),
        status: CandidateResolutionStatus = typer.Option(
            ...,
            "--status",
            case_sensitive=False,
            help="Manual outcome: assigned, not_entity or revoked.",
        ),
        scope: CandidateResolutionScope = typer.Option(
            CandidateResolutionScope.SINGLE,
            "--scope",
            case_sensitive=False,
            help="Scope: single or exact_canonical.",
        ),
        entity_id: int | None = typer.Option(
            None,
            "--entity-id",
            min=1,
            help=(
                "Existing entity to link. Omit on the first assigned "
                "revision to create a new entity."
            ),
        ),
        reason: str = typer.Option(
            ...,
            "--reason",
            help="Evidence-based reason for the identity decision.",
        ),
        reviewer: str = typer.Option(
            ...,
            "--reviewer",
            help="Identifier of the human reviewer.",
        ),
) -> None:
    """Assign, exclude or revoke an explicit candidate decision."""

    result = resolve_candidate_identity(
        candidate_id=candidate_id,
        entity_id=entity_id,
        decision=ManualCandidateResolutionDecision(
            status=status,
            scope=scope,
            reason=reason,
            reviewer=reviewer,
        ),
    )
    matched_ids = ",".join(
        str(item) for item in result.matched_candidate_ids
    )
    assigned_ids = ",".join(
        str(item) for item in result.newly_assigned_candidate_ids
    )
    typer.echo(
        f"decision_id={result.decision_id} "
        f"revision={result.revision} "
        f"supersedes={result.supersedes_decision_id} "
        f"status={result.status.value} "
        f"scope={result.scope.value} "
        f"seed_candidate_id={result.seed_entity_candidate_id} "
        f"entity_id={result.entity_id or 'none'} "
        f"entity_created={str(result.entity_created).lower()} "
        f"type={result.entity_type} "
        f"canonical_name={result.canonical_name!r} "
        f"matched_candidate_ids={matched_ids} "
        f"newly_assigned_candidate_ids={assigned_ids or 'none'}"
    )


@app.command()
def candidate_resolution_queue(
        document_version_id: int | None = typer.Option(
            None,
            "--document-version-id",
            min=1,
            help=(
                "Exact document version to review. Omit to select the "
                "most readily completable version."
            ),
        ),
        limit: int = typer.Option(
            20,
            min=1,
            help="Maximum number of unresolved canonical groups to show.",
        ),
        contexts: int = typer.Option(
            2,
            "--contexts",
            min=1,
            help="Maximum number of source contexts per group.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type boundary.",
        ),
) -> None:
    """Show an actionable queue for completing one document."""

    queue = get_candidate_resolution_queue(
        document_version_id=document_version_id,
        limit=limit,
        contexts_per_group=contexts,
        entity_type=entity_type,
    )
    readiness = queue.readiness
    typer.echo(
        f"document_version_id={queue.document_version_id} "
        f"document_id={queue.document_id} "
        f"version={queue.version_number} "
        f"type={readiness.entity_type.value if readiness.entity_type else 'all'} "
        f"status={readiness.status.value} "
        f"candidates={readiness.candidate_count} "
        f"safe_resolved={readiness.safe_resolved_count} "
        f"not_entity={readiness.not_entity_count} "
        f"unassigned={readiness.unassigned_count} "
        f"blocked={readiness.blocked_count} "
        f"invalid_provenance={readiness.invalid_provenance_count} "
        f"groups={queue.unresolved_group_count} "
        f"shown={queue.shown_group_count}"
    )
    typer.echo(
        f"document title={queue.title!r} "
        f"language={queue.language or 'unknown'} "
        f"identifier={queue.identifier_value!r}"
    )
    for group in queue.groups:
        entity_ids = ",".join(
            str(item) for item in group.assigned_entity_ids
        )
        surfaces = " | ".join(group.surface_variants)
        typer.echo(
            f"group seed_candidate_id={group.seed_entity_candidate_id} "
            f"type={group.entity_type.value} "
            f"canonical={group.canonical_text!r} "
            f"document_candidates={group.document_candidate_count} "
            f"corpus_candidates={group.corpus_candidate_count} "
            f"corpus_unassigned={group.corpus_unassigned_count} "
            f"corpus_not_entity={group.corpus_not_entity_count} "
            f"corpus_invalid_provenance="
            f"{group.corpus_invalid_provenance_count} "
            f"exact_scope={group.exact_scope_state.value} "
            f"assigned_entity_ids={entity_ids or 'none'} "
            f"surfaces={surfaces!r}"
        )
        for context in group.contexts:
            typer.echo(
                f"context candidate_id={context.entity_candidate_id} "
                f"mention_id={context.entity_mention_id} "
                f"span={context.start_char}:{context.end_char} "
                f"surface={context.surface_text!r} "
                f"text={context.context_text!r}"
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
        f"shown={len(report.items)} "
        f"candidate_shown={len(report.candidate_items)} "
        f"not_entity_shown={len(report.not_entity_items)}"
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
    for item in report.candidate_items:
        applied = ",".join(
            str(decision_id)
            for decision_id in item.applied_decision_ids
        )
        typer.echo(
            f"candidate_resolution entity_id={item.entity_id} "
            f"type={item.entity_type.value} "
            f"canonical_name={item.canonical_name!r} "
            f"safe_for_downstream="
            f"{str(item.safe_for_downstream_use).lower()} "
            f"seed_candidate_id={item.seed_candidate_id} "
            f"seed_canonical={item.seed_canonical_text!r} "
            f"scope={item.scope.value} "
            f"applied_decision_ids={applied or 'none'} "
            f"latest_decision_id={item.latest_decision_id} "
            f"latest_revision={item.latest_revision} "
            f"latest_status={item.latest_status.value} "
            f"validity={item.validity.value}"
        )
    for item in report.not_entity_items:
        matched = ",".join(
            str(candidate_id)
            for candidate_id in item.matched_candidate_ids
        )
        typer.echo(
            f"not_entity seed_candidate_id={item.seed_candidate_id} "
            f"type={item.entity_type.value} "
            f"canonical={item.canonical_text!r} "
            f"scope={item.scope.value} "
            f"matched_candidate_ids={matched or 'none'} "
            f"applied_decision_id={item.applied_decision_id} "
            f"latest_decision_id={item.latest_decision_id} "
            f"latest_revision={item.latest_revision} "
            f"latest_status={item.latest_status.value} "
            f"reviewer={item.reviewer!r} "
            f"reason={item.reason!r} "
            f"validity={item.validity.value} "
            f"issue={item.issue!r}"
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
        candidate_decision_ids = ",".join(
            str(
                link.latest_candidate_resolution_decision_id
            )
            for link in entity.active_candidate_resolutions
        )
        typer.echo(
            f"entity entity_id={entity.entity_id} "
            f"type={entity.entity_type.value} "
            f"canonical_name={entity.canonical_name!r} "
            f"canonical_candidate_id="
            f"{entity.canonical_entity_candidate_id} "
            f"candidate_ids={candidate_ids} "
            f"active_decision_ids={decision_ids or 'none'} "
            f"active_candidate_decision_ids="
            f"{candidate_decision_ids or 'none'}"
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
        candidate_decision_ids = ",".join(
            str(
                link.latest_candidate_resolution_decision_id
            )
            for link in entity.active_candidate_resolutions
        )
        typer.echo(
            f"entity entity_id={entity.entity_id} "
            f"type={entity.entity_type.value} "
            f"canonical_name={entity.canonical_name!r} "
            f"canonical_candidate_id="
            f"{entity.canonical_entity_candidate_id} "
            f"occurrences={len(entity.occurrences)} "
            f"active_decision_ids={decision_ids or 'none'} "
            f"active_candidate_decision_ids="
            f"{candidate_decision_ids or 'none'}"
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
                f"{(
                    occurrence.assigned_by_alias_decision_id
                    or occurrence
                    .assigned_by_candidate_resolution_decision_id
                    or 'none'
                )} "
                f"candidate_assignment_decision_id="
                f"{(
                    occurrence
                    .assigned_by_candidate_resolution_decision_id
                ) or 'none'}"
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
            f"{(
                item.assigned_by_alias_decision_id
                or item.assigned_by_candidate_resolution_decision_id
                or 'none'
            )} "
            f"blocking_validities={blocking or 'none'} "
            f"span={span} "
            f"surface={item.surface_text!r} "
            f"canonical={item.canonical_text!r} "
            f"provenance_issue={item.provenance_issue!r} "
            f"candidate_assignment_decision_id="
            f"{item.assigned_by_candidate_resolution_decision_id or 'none'} "
            f"not_entity_decision_id="
            f"{item.not_entity_decision_id or 'none'} "
            f"not_entity_revision={item.not_entity_revision or 'none'}"
        )


@app.command()
def document_entity_readiness(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version whose entity readiness is checked.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type filter applied before readiness.",
        ),
) -> None:
    """Check whether entity resolution is safe for downstream use."""

    report = get_document_entity_readiness(
        document_version_id=document_version_id,
        entity_type=entity_type,
    )
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"document_id={report.document_id} "
        f"version={report.version_number} "
        f"type={report.entity_type.value if report.entity_type else 'all'} "
        f"status={report.status.value} "
        f"ready={str(report.ready_for_downstream_use).lower()} "
        f"candidates={report.candidate_count} "
        f"safe_resolved={report.safe_resolved_count} "
        f"not_entity={report.not_entity_count} "
        f"unassigned={report.unassigned_count} "
        f"blocked={report.blocked_count} "
        f"invalid_provenance={report.invalid_provenance_count}"
    )


@app.command()
def corpus_entity_readiness(
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of matching document versions to show.",
        ),
        status: DocumentEntityReadinessStatus | None = typer.Option(
            None,
            "--status",
            help="Optional readiness status filter for detailed rows.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type filter applied before readiness.",
        ),
) -> None:
    """Audit entity readiness across every document version."""

    report = get_corpus_entity_readiness(
        limit=limit,
        status=status,
        entity_type=entity_type,
    )
    typer.echo(
        f"document_versions={report.document_version_count} "
        f"ready={report.ready_document_version_count} "
        f"unsafe={report.unsafe_document_version_count} "
        f"matched={report.matched_document_version_count} "
        f"shown={len(report.items)} "
        f"type={report.entity_type.value if report.entity_type else 'all'}"
    )
    typer.echo(
        f"candidates={report.candidate_count} "
        f"safe_resolved={report.safe_resolved_count} "
        f"not_entity={report.not_entity_count} "
        f"unassigned={report.unassigned_count} "
        f"blocked={report.blocked_count} "
        f"invalid_provenance={report.invalid_provenance_count}"
    )
    for count in report.counts_by_status:
        typer.echo(
            f"readiness={count.status.value} "
            f"document_versions={count.count}"
        )
    for item in report.items:
        typer.echo(
            f"document document_version_id={item.document_version_id} "
            f"document_id={item.document_id} "
            f"version={item.version_number} "
            f"status={item.status.value} "
            f"ready={str(item.ready_for_downstream_use).lower()} "
            f"candidates={item.candidate_count} "
            f"safe_resolved={item.safe_resolved_count} "
            f"not_entity={item.not_entity_count} "
            f"unassigned={item.unassigned_count} "
            f"blocked={item.blocked_count} "
            f"invalid_provenance={item.invalid_provenance_count}"
        )


@app.command()
def ready_document_versions(
        limit: int = typer.Option(
            50,
            min=1,
            help="Maximum number of downstream-safe versions to select.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type readiness boundary.",
        ),
) -> None:
    """Select only document versions safe for entity-dependent analysis."""

    selection = select_ready_document_versions(
        limit=limit,
        entity_type=entity_type,
    )
    typer.echo(
        f"ready_document_versions="
        f"{selection.ready_document_version_count} "
        f"selected={selection.selected_document_version_count} "
        f"type="
        f"{selection.entity_type.value if selection.entity_type else 'all'}"
    )
    for item in selection.items:
        typer.echo(
            f"document document_version_id={item.document_version_id} "
            f"document_id={item.document_id} "
            f"version={item.version_number} "
            f"candidates={item.candidate_count} "
            f"safe_resolved={item.safe_resolved_count} "
            f"not_entity={item.not_entity_count}"
        )


@app.command()
def document_analysis_input(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact ready document version to bundle.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type readiness boundary.",
        ),
) -> None:
    """Build one atomic input for entity-dependent document analysis."""

    bundle = get_document_analysis_input(
        document_version_id=document_version_id,
        entity_type=entity_type,
    )
    document = bundle.document
    typer.echo(
        f"document_version_id={document.document_version_id} "
        f"document_id={document.document_id} "
        f"version={document.version_number} "
        f"type={bundle.entity_type.value if bundle.entity_type else 'all'} "
        f"status={bundle.readiness.status.value} "
        f"candidates={bundle.readiness.candidate_count} "
        f"entities={bundle.entities.resolved_entity_count} "
        f"occurrences={bundle.entities.resolved_occurrence_count} "
        f"not_entity={bundle.readiness.not_entity_count}"
    )
    typer.echo(
        f"text_artifact_id={bundle.text.derived_artifact_id} "
        f"text_type={bundle.text.artifact_type.value} "
        f"text_hash={bundle.text.content_hash} "
        f"characters={bundle.text.character_count} "
        f"raw_artifact_id={document.raw_artifact_id} "
        f"raw_hash={document.raw_content_hash}"
    )
    for entity in bundle.entities.items:
        typer.echo(
            f"entity entity_id={entity.entity_id} "
            f"entity_type={entity.entity_type.value} "
            f"canonical_name={entity.canonical_name!r} "
            f"occurrences={len(entity.occurrences)} "
            f"active_alias_resolutions={len(entity.active_resolutions)} "
            f"active_candidate_resolutions="
            f"{len(entity.active_candidate_resolutions)}"
        )
    for item in bundle.not_entity_resolutions:
        typer.echo(
            f"not_entity entity_candidate_id={item.entity_candidate_id} "
            f"entity_mention_id={item.entity_mention_id} "
            f"type={item.entity_type.value} "
            f"canonical={item.canonical_text!r} "
            f"surface={item.surface_text!r} "
            f"span={item.start_char}:{item.end_char} "
            f"decision_id={item.decision_id} "
            f"revision={item.revision} scope={item.scope} "
            f"reviewer={item.reviewer!r} reason={item.reason!r}"
        )


@app.command()
def compare_document_event_similarity(
        left_document_version_id: int = typer.Option(
            ...,
            "--left-document-version-id",
            min=1,
            help="First exact ready document version.",
        ),
        right_document_version_id: int = typer.Option(
            ...,
            "--right-document-version-id",
            min=1,
            help="Second exact ready document version.",
        ),
        temporal_window_hours: float = typer.Option(
            72.0,
            "--temporal-window-hours",
            min=0.000001,
            help="Linear temporal-decay window in hours.",
        ),
) -> None:
    """Show explainable pair evidence without assigning an event."""

    try:
        result = get_document_pair_event_similarity(
            left_document_version_id=left_document_version_id,
            right_document_version_id=right_document_version_id,
            configuration=EventSimilarityConfiguration(
                temporal_window_hours=temporal_window_hours,
            ),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"left_document_version_id={result.left_document_version_id} "
        f"left_document_id={result.left_document_id} "
        f"right_document_version_id={result.right_document_version_id} "
        f"right_document_id={result.right_document_id}"
    )
    typer.echo(
        "combined_score="
        f"{result.combined_score if result.combined_score is not None else 'none'} "
        f"available_weight={result.available_weight} "
        "same_event_decision=none"
    )
    shared_entity_ids = ",".join(
        str(item) for item in result.shared_entity_ids
    ) or "none"
    typer.echo(f"shared_entity_ids={shared_entity_ids}")
    for signal in result.signals:
        typer.echo(
            f"signal={signal.name!r} "
            f"available={str(signal.available).lower()} "
            f"score={signal.score if signal.score is not None else 'none'} "
            f"configured_weight={signal.configured_weight} "
            f"effective_weight={signal.effective_weight} "
            "contribution="
            f"{signal.contribution if signal.contribution is not None else 'none'} "
            f"explanation={signal.explanation!r}"
        )
    for limitation in result.limitations:
        typer.echo(f"limitation={limitation!r}")


@app.command()
def event_fragments(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version whose candidate spans should be shown.",
        ),
) -> None:
    """Show source-anchored candidates without assigning any event."""

    try:
        report = get_event_fragments(
            document_version_id=document_version_id,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"event_fragments={report.event_fragment_count} "
        f"event_assignments={report.event_assignment_count}"
    )
    for item in report.items:
        typer.echo(
            f"event_fragment_id={item.event_fragment_id} "
            f"text_artifact_id={item.text_derived_artifact_id} "
            f"span={item.start_char}:{item.end_char} "
            f"text_hash={item.text_hash} method={item.method!r} "
            f"method_version={item.method_version!r} "
            f"created_by={item.created_by!r} "
            f"rationale={item.rationale!r} event_assignment=none"
        )
        for limitation in item.quality_limitations:
            typer.echo(
                f"event_fragment_id={item.event_fragment_id} "
                f"limitation={limitation!r}"
            )


@app.command("youtube-transcript-tracks")
def youtube_transcript_tracks_command(
        youtube_url: str = typer.Option(
            ...,
            "--youtube-url",
            help="Exact youtube.com or youtu.be video URL to inspect.",
        ),
) -> None:
    """List exact WebVTT tracks without retrieving or persisting one."""

    try:
        catalog = YouTubeTranscriptSource().catalog(youtube_url)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"youtube_video_id={catalog.video_id!r} "
        f"provider={catalog.provider!r} "
        f"provider_version={catalog.provider_version!r} "
        f"tracks={len(catalog.tracks)} title={catalog.title!r}"
    )
    for track in catalog.tracks:
        typer.echo(
            f"track_id={track.track_id!r} name={track.name!r} "
            f"transcript_kind={track.transcript_kind.value!r} "
            f"transcript_format={track.transcript_format.value!r}"
        )


@app.command("ingest-youtube-transcript")
def ingest_youtube_transcript_command(
        document_version_id: int = typer.Option(
            ..., "--document-version-id", min=1,
            help="Video document version to which the transcript belongs.",
        ),
        youtube_url: str = typer.Option(
            ...,
            "--youtube-url",
            help="Exact youtube.com or youtu.be video URL.",
        ),
        track_id: str = typer.Option(
            ...,
            "--track-id",
            help="Exact track id shown by youtube-transcript-tracks.",
        ),
        allow_auto_generated: bool = typer.Option(
            False,
            "--allow-auto-generated",
            help="Explicitly permit an automatically generated caption track.",
        ),
        allow_cross_location: bool = typer.Option(
            False,
            "--allow-cross-location",
            help=(
                "Attach a YouTube mirror to a non-matching document URI and "
                "record that equivalence remains operator-asserted."
            ),
        ),
) -> None:
    """Retrieve one exact YouTube caption track and preserve its provenance."""

    try:
        result = ingest_youtube_transcript(
            document_version_id=document_version_id,
            youtube_url=youtube_url,
            track_id=track_id,
            allow_auto_generated=allow_auto_generated,
            allow_cross_location=allow_cross_location,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    ingestion = result.ingestion
    typer.echo(
        f"document_version_id={ingestion.document_version_id} "
        f"youtube_video_id={result.video_id!r} track_id={result.track_id!r} "
        f"cross_location={str(result.cross_location).lower()} "
        f"transcript_acquisition_id={ingestion.transcript_acquisition_id} "
        f"raw_artifact_id={ingestion.raw_artifact_id} "
        f"transcript_artifact_id={ingestion.transcript_artifact_id} "
        f"character_count={ingestion.character_count} "
        f"language={ingestion.language!r} "
        f"transcript_kind={ingestion.transcript_kind.value!r}"
    )
    typer.echo(
        f"raw_content_hash={ingestion.raw_content_hash} "
        f"text_content_hash={ingestion.text_content_hash}"
    )
    for limitation in ingestion.quality_limitations:
        typer.echo(f"transcript_limitation={limitation!r}")


@app.command("ingest-transcript")
def ingest_transcript_command(
        document_version_id: int = typer.Option(
            ..., "--document-version-id", min=1,
            help="Video document version to which the transcript belongs.",
        ),
        transcript_file: Path = typer.Option(
            ..., "--transcript-file", exists=True, dir_okay=False,
            readable=True, help="Exact UTF-8 provider output to preserve.",
        ),
        provider: str = typer.Option(
            ..., "--provider", help="Transcript provider or acquisition tool.",
        ),
        provider_version: str = typer.Option(
            ..., "--provider-version", help="Exact provider/tool version.",
        ),
        requested_location: str = typer.Option(
            ..., "--requested-location",
            help="Video, caption-track, API, or archive location requested.",
        ),
        retrieved_at: str = typer.Option(
            ..., "--retrieved-at", help="RFC 3339 retrieval time with timezone.",
        ),
        language: str = typer.Option(
            ..., "--language", help="BCP 47 transcript language tag.",
        ),
        transcript_kind: TranscriptKind = typer.Option(
            TranscriptKind.UNKNOWN,
            "--transcript-kind",
            case_sensitive=False,
            help="Upstream authorship class of the transcript.",
        ),
        transcript_format: TranscriptFormat = typer.Option(
            TranscriptFormat.PLAIN_TEXT,
            "--transcript-format",
            case_sensitive=False,
            help="Serialization of the imported transcript bytes.",
        ),
        media_type: str = typer.Option(
            "text/plain; charset=utf-8",
            "--media-type",
            help="Media type of the exact imported bytes.",
        ),
        resolved_location: str | None = typer.Option(
            None, "--resolved-location",
            help="Final location after redirects or provider resolution.",
        ),
        external_identifier: str | None = typer.Option(
            None, "--external-identifier",
            help="Provider track or media identifier, when known.",
        ),
) -> None:
    """Preserve transcript bytes and create a normalized text artifact."""

    try:
        normalized_retrieved_at = datetime.fromisoformat(
            retrieved_at.replace("Z", "+00:00")
        )
        if (
                normalized_retrieved_at.tzinfo is None
                or normalized_retrieved_at.utcoffset() is None
        ):
            raise ValueError("retrieved_at must include a timezone.")
        result = ingest_transcript_file(
            document_version_id=document_version_id,
            transcript_file=transcript_file,
            provider=provider,
            provider_version=provider_version,
            requested_location=requested_location,
            resolved_location=resolved_location,
            external_identifier=external_identifier,
            retrieved_at=normalized_retrieved_at,
            language=language,
            transcript_kind=transcript_kind,
            transcript_format=transcript_format,
            media_type=media_type,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"document_version_id={result.document_version_id} "
        f"transcript_acquisition_id={result.transcript_acquisition_id} "
        f"raw_artifact_id={result.raw_artifact_id} "
        f"transcript_artifact_id={result.transcript_artifact_id} "
        f"character_count={result.character_count} "
        f"language={result.language!r} "
        f"transcript_kind={result.transcript_kind.value!r} "
        f"transcript_format={result.transcript_format.value!r}"
    )
    typer.echo(
        f"raw_content_hash={result.raw_content_hash} "
        f"text_content_hash={result.text_content_hash}"
    )
    for limitation in result.quality_limitations:
        typer.echo(f"transcript_limitation={limitation!r}")


@app.command("inspect-document-text")
def inspect_document_text_blocks(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version whose text structure should be shown.",
        ),
        text_artifact_id: int | None = typer.Option(
            None,
            "--text-artifact-id",
            min=1,
            help="Exact text artifact when the version has more than one.",
        ),
        max_block_chars: int = typer.Option(
            400,
            "--max-block-chars",
            min=40,
            max=5000,
            help="Maximum characters printed for each block preview.",
        ),
) -> None:
    """Show reproducible paragraph blocks and exact character offsets."""

    try:
        report = inspect_document_text(
            document_version_id=document_version_id,
            text_derived_artifact_id=text_artifact_id,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"text_artifact_id={report.text_derived_artifact_id} "
        f"character_count={report.character_count} "
        f"text_hash={report.text_hash} blocks={len(report.blocks)} "
        "event_text_readiness="
        f"{report.event_text_readiness.status.value!r} "
        "ready_for_event_analysis="
        f"{str(report.event_text_readiness.ready_for_event_analysis).lower()}"
    )
    for reason in report.event_text_readiness.reasons:
        typer.echo(f"event_text_blocker={reason!r}")
    for limitation in report.event_text_readiness.limitations:
        typer.echo(f"event_text_limitation={limitation!r}")
    for block in report.blocks:
        preview = block.text
        truncated = len(preview) > max_block_chars
        if truncated:
            preview = preview[:max_block_chars]
        typer.echo(
            f"block={block.block_index} "
            f"span={block.start_char}:{block.end_char} "
            f"heading_candidate={str(block.heading_candidate).lower()} "
            f"text_hash={block.text_hash} truncated={str(truncated).lower()} "
            f"text={json.dumps(preview, ensure_ascii=False)}"
        )


@app.command("inspect-transcript-timeline")
def inspect_transcript_timeline_command(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version owning the transcript artifact.",
        ),
        text_artifact_id: int = typer.Option(
            ...,
            "--text-artifact-id",
            min=1,
            help="Exact transcript artifact whose cue map should be shown.",
        ),
        max_cue_chars: int = typer.Option(
            240,
            "--max-cue-chars",
            min=40,
            max=2000,
            help="Maximum normalized characters printed for each cue.",
        ),
        start_cue: int = typer.Option(
            1,
            "--start-cue",
            min=1,
            help="One-based first cue to print after validating the full map.",
        ),
        limit: int = typer.Option(
            50,
            "--limit",
            min=1,
            max=500,
            help="Maximum cues to print after full-map validation.",
        ),
) -> None:
    """Validate and show normalized-text-to-caption-cue provenance."""

    try:
        report = inspect_transcript_timeline(
            document_version_id=document_version_id,
            transcript_artifact_id=text_artifact_id,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if start_cue > len(report.items):
        raise typer.BadParameter(
            f"start_cue exceeds the available cue count: {len(report.items)}."
        )
    shown_items = report.items[start_cue - 1:start_cue - 1 + limit]
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"text_artifact_id={report.transcript_artifact_id} "
        f"transcript_acquisition_id={report.transcript_acquisition_id} "
        f"raw_artifact_id={report.raw_artifact_id} "
        f"character_count={report.character_count} "
        f"text_hash={report.text_hash} cues={len(report.items)} "
        f"contributing_cues={report.contributing_cue_count} "
        f"suppressed_cues={report.suppressed_cue_count} "
        f"shown_cues={len(shown_items)} "
        f"shown_range={start_cue}:{start_cue + len(shown_items) - 1} "
        "cue_provenance_schema_version="
        f"{report.cue_provenance_schema_version!r} "
        f"time_unit={report.time_unit!r}"
    )
    for item in shown_items:
        cue_text = item.normalized_cue_text
        truncated = len(cue_text) > max_cue_chars
        if truncated:
            cue_text = cue_text[:max_cue_chars]
        output_span = (
            "none"
            if item.output_start_char is None
            else f"{item.output_start_char}:{item.output_end_char}"
        )
        gap_before = (
            "none" if item.gap_before_ms is None else str(item.gap_before_ms)
        )
        suppression = item.suppression_reason or "none"
        typer.echo(
            f"cue={item.cue_index} "
            f"source_block={item.source_block_index} "
            f"time_ms={item.start_ms}:{item.end_ms} "
            f"duration_ms={item.duration_ms} "
            f"gap_before_ms={gap_before} output_span={output_span} "
            f"removed_prefix_words={item.removed_prefix_word_count} "
            "removed_internal_words="
            f"{item.removed_internal_overlap_word_count} "
            f"suppression={suppression!r} "
            f"source_text_hash={item.source_text_hash} "
            f"truncated={str(truncated).lower()} "
            f"text={json.dumps(cue_text, ensure_ascii=False)}"
        )


@app.command("segment-event-fragments")
def segment_event_fragments_command(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact document version to segment structurally.",
        ),
        text_artifact_id: int | None = typer.Option(
            None,
            "--text-artifact-id",
            min=1,
            help="Exact text artifact when the version has more than one.",
        ),
        persist: bool = typer.Option(
            False,
            "--persist",
            help="Persist the proposals as candidates; never assign events.",
        ),
) -> None:
    """Preview or persist deterministic structural fragment candidates."""

    try:
        report = segment_event_fragments(
            document_version_id=document_version_id,
            text_derived_artifact_id=text_artifact_id,
            persist=persist,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"document_version_id={report.document_version_id} "
        f"text_artifact_id={report.text_derived_artifact_id} "
        f"fragments={report.fragment_count} "
        f"persisted={str(report.persisted).lower()} "
        f"method={report.method!r} "
        f"method_version={report.method_version!r} "
        f"boundary_basis={report.boundary_basis!r} "
        "event_text_readiness="
        f"{report.event_text_readiness.status.value!r} "
        "ready_for_event_analysis="
        f"{str(report.event_text_readiness.ready_for_event_analysis).lower()} "
        "event_assignments=0"
    )
    for index, item in enumerate(report.items, start=1):
        typer.echo(
            f"fragment={index} "
            "event_fragment_id="
            f"{item.event_fragment_id if item.event_fragment_id is not None else 'none'} "
            f"span={item.start_char}:{item.end_char} "
            f"text_hash={item.text_hash} "
            f"rationale={item.rationale!r} event_assignment=none"
        )
        for limitation in item.quality_limitations:
            typer.echo(f"fragment={index} limitation={limitation!r}")


@app.command()
def prepare_analysis(
        document_version_id: int = typer.Option(
            ...,
            "--document-version-id",
            min=1,
            help="Exact ready document version to fingerprint.",
        ),
        analysis_method: str = typer.Option(
            ...,
            "--method",
            help="Stable analytical method identifier.",
        ),
        analysis_method_version: str = typer.Option(
            ...,
            "--method-version",
            help="Exact analytical method version.",
        ),
        configuration_json: str = typer.Option(
            "{}",
            "--configuration-json",
            help="Analysis configuration as one JSON object.",
        ),
        entity_type: EntityType | None = typer.Option(
            None,
            "--type",
            help="Optional entity type readiness boundary.",
        ),
) -> None:
    """Persist one reproducible analysis-run input contract."""

    try:
        configuration = json.loads(configuration_json)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(
            "configuration-json must be valid JSON."
        ) from error
    if not isinstance(configuration, dict):
        raise typer.BadParameter(
            "configuration-json must contain one JSON object."
        )

    result = prepare_analysis_run(
        document_version_id=document_version_id,
        analysis_method=analysis_method,
        analysis_method_version=analysis_method_version,
        configuration=configuration,
        entity_type=entity_type,
    )
    typer.echo(
        f"analysis_run_id={result.analysis_run_id} "
        f"created={str(result.created).lower()} "
        f"status={result.status.value} "
        f"document_version_id={result.document_version_id} "
        f"type={result.entity_type_scope} "
        f"method={result.analysis_method!r} "
        f"method_version={result.analysis_method_version!r} "
        f"software_version={result.software_version!r}"
    )
    typer.echo(
        f"input_schema={result.input_schema_version} "
        f"input_fingerprint={result.input_fingerprint} "
        f"configuration_hash={result.configuration_hash} "
        f"candidates={result.candidate_count} "
        f"entities={result.resolved_entity_count} "
        f"occurrences={result.resolved_occurrence_count} "
        f"not_entity={result.not_entity_count}"
    )


@app.command()
def execute_analysis(
        analysis_run_id: int = typer.Option(
            ...,
            "--analysis-run-id",
            min=1,
            help="Exact prepared analysis run to execute.",
        ),
        retry_failed: bool = typer.Option(
            False,
            "--retry-failed",
            help="Retry a failed run without changing its input contract.",
        ),
) -> None:
    """Execute one registered method and persist its immutable result."""

    result = execute_analysis_run(
        analysis_run_id=analysis_run_id,
        retry_failed=retry_failed,
    )
    typer.echo(
        f"analysis_run_id={result.analysis_run_id} "
        f"analysis_result_id={result.analysis_result_id} "
        f"executed={str(result.executed).lower()} "
        f"status={result.status.value} "
        f"attempts={result.attempt_count} "
        f"method={result.analysis_method!r} "
        f"method_version={result.analysis_method_version!r} "
        f"software_version={result.software_version!r}"
    )
    typer.echo(
        f"result_schema={result.result_schema_version} "
        f"output_hash={result.output_hash} "
        f"warnings={result.warning_count} "
        f"evidence={result.evidence_count}"
    )


@app.command()
def analysis_result(
        analysis_run_id: int = typer.Option(
            ...,
            "--analysis-run-id",
            min=1,
            help="Completed analysis run whose result should be shown.",
        ),
) -> None:
    """Read one completed, hash-verified analytical result."""

    result = get_analysis_run_result(
        analysis_run_id=analysis_run_id,
    )
    typer.echo(
        f"analysis_run_id={result.analysis_run_id} "
        f"analysis_result_id={result.analysis_result_id} "
        f"status={result.status.value} attempts={result.attempt_count} "
        f"method={result.analysis_method!r} "
        f"method_version={result.analysis_method_version!r} "
        f"software_version={result.software_version!r}"
    )
    typer.echo(
        f"result_schema={result.result_schema_version} "
        f"output_hash={result.output_hash} "
        f"warnings={len(result.warnings)} "
        f"evidence={result.evidence_count} "
        f"evidence_set_hash={result.evidence_set_hash!r}"
    )
    typer.echo(
        "payload="
        + json.dumps(
            result.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    for warning in result.warnings:
        typer.echo(f"warning={warning!r}")


@app.command()
def analysis_evidence(
        analysis_run_id: int = typer.Option(
            ...,
            "--analysis-run-id",
            min=1,
            help="Completed analysis run whose evidence should be shown.",
        ),
) -> None:
    """Read ordered, hash-verified, source-located analytical evidence."""

    result = get_analysis_evidence(analysis_run_id=analysis_run_id)
    typer.echo(
        f"analysis_run_id={result.analysis_run_id} "
        f"analysis_result_id={result.analysis_result_id} "
        f"shown={len(result.evidence)} "
        f"evidence_set_hash={result.evidence_set_hash!r}"
    )
    for item in result.evidence:
        typer.echo(
            f"evidence={item.evidence_index} id={item.evidence_id} "
            f"schema={item.evidence_schema_version!r} "
            f"category={item.category!r} modality={item.modality!r} "
            f"hash={item.evidence_hash}"
        )
        typer.echo(
            "locator="
            + json.dumps(
                item.locator,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        typer.echo(
            "evidence_payload="
            + json.dumps(
                item.payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


@app.command()
def register_human_corpus_source(
        input_text: Path = typer.Option(
            ..., "--input-text", exists=True, dir_okay=False, readable=True,
            help="UTF-8 human-authored source text to preserve.",
        ),
        workspace_root: Path = typer.Option(
            ..., "--workspace-root", file_okay=False,
            help="Corpus intake root containing text, records and logs.",
        ),
        source_id: str = typer.Option(..., "--source-id"),
        language: str = typer.Option(..., "--language"),
        genre: str = typer.Option(..., "--genre"),
        source_group_id: str = typer.Option(..., "--source-group-id"),
        reference: str = typer.Option(
            ..., "--reference", help="Preserved publication or archive reference.",
        ),
        title: str = typer.Option(..., "--title", help="Published title."),
        author: str = typer.Option(..., "--author", help="Published byline."),
        publisher: str = typer.Option(..., "--publisher"),
        published_date: str = typer.Option(
            ..., "--published-date", help="Publication date in YYYY-MM-DD format.",
        ),
        text_scope: str = typer.Option(
            ..., "--text-scope",
            help="Preserved content boundary, such as article-body.",
        ),
        retrieved_at: str = typer.Option(
            ..., "--retrieved-at", help="RFC 3339 retrieval time with timezone.",
        ),
        acquisition_method: str = typer.Option(
            ..., "--acquisition-method", help="How the exact text was acquired.",
        ),
) -> None:
    """Register one provenance-supported human corpus source."""

    try:
        result = register_human_source(
            input_text,
            workspace_root=workspace_root,
            source_id=source_id,
            language=language,
            genre=genre,
            source_group_id=source_group_id,
            reference=reference,
            title=title,
            author=author,
            publisher=publisher,
            published_date=published_date,
            text_scope=text_scope,
            retrieved_at=retrieved_at,
            acquisition_method=acquisition_method,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"source_id={result.source_id!r} label={result.label} "
        f"content_sha256={result.content_sha256}"
    )
    typer.echo(
        f"text={str(result.text_path)!r} record={str(result.record_path)!r}"
    )


@app.command()
def register_synthetic_corpus_source(
        input_text: Path = typer.Option(
            ..., "--input-text", exists=True, dir_okay=False, readable=True,
            help="UTF-8 generated source text to preserve.",
        ),
        prompt_file: Path = typer.Option(
            ..., "--prompt-file", exists=True, dir_okay=False, readable=True,
            help="Exact UTF-8 prompt artifact used for generation.",
        ),
        workspace_root: Path = typer.Option(
            ..., "--workspace-root", file_okay=False,
            help="Corpus intake root containing text, records and logs.",
        ),
        source_id: str = typer.Option(..., "--source-id"),
        language: str = typer.Option(..., "--language"),
        genre: str = typer.Option(..., "--genre"),
        source_group_id: str = typer.Option(..., "--source-group-id"),
        generated_at: str = typer.Option(
            ..., "--generated-at", help="RFC 3339 generation time with timezone.",
        ),
        provider: str = typer.Option(..., "--provider"),
        model: str = typer.Option(..., "--model"),
        model_version: str = typer.Option(..., "--model-version"),
        generation_parameters_json: str = typer.Option(
            "{}", "--generation-parameters-json",
            help="Exact generation parameters as one JSON object.",
        ),
) -> None:
    """Register generated text with its prompt and generation log."""

    try:
        parameters = json.loads(generation_parameters_json)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(
            "generation-parameters-json must be valid JSON."
        ) from error
    if not isinstance(parameters, dict):
        raise typer.BadParameter(
            "generation-parameters-json must contain one JSON object."
        )
    result = register_synthetic_source(
        input_text,
        prompt_file,
        workspace_root=workspace_root,
        source_id=source_id,
        language=language,
        genre=genre,
        source_group_id=source_group_id,
        generated_at=generated_at,
        provider=provider,
        model=model,
        model_version=model_version,
        generation_parameters=parameters,
    )
    typer.echo(
        f"source_id={result.source_id!r} label={result.label} "
        f"content_sha256={result.content_sha256}"
    )
    typer.echo(
        f"text={str(result.text_path)!r} prompt={str(result.prompt_path)!r} "
        f"generation_log={str(result.generation_log_path)!r} "
        f"record={str(result.record_path)!r}"
    )


@app.command()
def assemble_synthetic_corpus_manifest(
        workspace_root: Path = typer.Option(
            ..., "--workspace-root", exists=True, file_okay=False, readable=True,
            help="Corpus intake root containing registered source records.",
        ),
        output_jsonl: Path = typer.Option(
            ..., "--output-jsonl", dir_okay=False,
            help="New deterministic source-record manifest.",
        ),
        split_salt: str = typer.Option(
            ..., "--split-salt",
            help="Dataset/version salt used to validate group split assignment.",
        ),
        train_ratio: float = typer.Option(0.6, "--train-ratio", min=0.01, max=0.98),
        calibration_ratio: float = typer.Option(
            0.2, "--calibration-ratio", min=0.01, max=0.98,
        ),
) -> None:
    """Assemble and validate registered records as one manifest."""

    result = assemble_source_manifest(
        workspace_root=workspace_root,
        output_jsonl=output_jsonl,
        split_salt=split_salt,
        train_ratio=train_ratio,
        calibration_ratio=calibration_ratio,
    )
    typer.echo(
        f"schema={result['schema']!r} intake={result['intake_version']!r} "
        f"records={result['records']} manifest_hash={result['manifest_hash']}"
    )
    typer.echo(
        "labels=" + json.dumps(result["labels"], sort_keys=True)
        + " splits=" + json.dumps(result["splits"], sort_keys=True)
    )
    typer.echo(f"output={str(output_jsonl)!r}")


@app.command()
def inspect_synthetic_corpus_intake(
        workspace_root: Path = typer.Option(
            ..., "--workspace-root", exists=True, file_okay=False, readable=True,
            help="Corpus intake root containing registered source records.",
        ),
        split_salt: str = typer.Option(
            ..., "--split-salt",
            help="Dataset/version salt used to preview group split assignment.",
        ),
        train_ratio: float = typer.Option(0.6, "--train-ratio", min=0.01, max=0.98),
        calibration_ratio: float = typer.Option(
            0.2, "--calibration-ratio", min=0.01, max=0.98,
        ),
) -> None:
    """Verify registered artifacts and preview corpus build readiness."""

    try:
        result = inspect_source_intake(
            workspace_root=workspace_root,
            split_salt=split_salt,
            train_ratio=train_ratio,
            calibration_ratio=calibration_ratio,
        )
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"records={result.records} groups={result.groups} "
        f"ready_for_build={str(result.ready_for_build).lower()}"
    )
    typer.echo(
        "labels=" + json.dumps(result.labels, sort_keys=True)
        + " splits=" + json.dumps(result.splits, sort_keys=True)
    )
    typer.echo(
        "split_labels=" + json.dumps(result.split_labels, sort_keys=True)
    )
    for source in result.sources:
        typer.echo(
            f"source={source['source_id']!r} label={source['label']} "
            f"split={source['split']} group={source['source_group_id']!r} "
            f"eligible={str(source['eligible_for_scoring']).lower()} "
            f"words={source['word_count']} sentences={source['sentence_count']}"
        )
    if result.missing_split_labels:
        typer.echo("missing=" + ",".join(result.missing_split_labels))
    if result.ineligible_sources:
        typer.echo("ineligible=" + ",".join(result.ineligible_sources))
    if result.unsupported_language_sources:
        typer.echo(
            "unsupported_language="
            + ",".join(result.unsupported_language_sources)
        )


@app.command()
def build_synthetic_corpus(
        manifest_jsonl: Path = typer.Option(
            ...,
            "--manifest-jsonl",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Provenance-bearing source-record JSONL manifest.",
        ),
        source_root: Path = typer.Option(
            ...,
            "--source-root",
            exists=True,
            file_okay=False,
            readable=True,
            help="Root containing immutable UTF-8 source text files.",
        ),
        output_jsonl: Path = typer.Option(
            ...,
            "--output-jsonl",
            dir_okay=False,
            help="New calibration-corpus JSONL file.",
        ),
        receipt_json: Path = typer.Option(
            ...,
            "--receipt-json",
            dir_okay=False,
            help="New hash-bound corpus build receipt.",
        ),
        split_salt: str = typer.Option(
            ...,
            "--split-salt",
            help="Stable public dataset/version salt for group split assignment.",
        ),
        train_ratio: float = typer.Option(
            0.6,
            "--train-ratio",
            min=0.01,
            max=0.98,
            help="Deterministic train bucket share.",
        ),
        calibration_ratio: float = typer.Option(
            0.2,
            "--calibration-ratio",
            min=0.01,
            max=0.98,
            help="Deterministic calibration bucket share.",
        ),
) -> None:
    """Build a provenance-bound, deduplicated calibration corpus."""

    build = build_corpus_from_manifest(
        manifest_jsonl,
        source_root=source_root,
        split_salt=split_salt,
        train_ratio=train_ratio,
        calibration_ratio=calibration_ratio,
    )
    receipt = write_corpus_build(
        build,
        output_jsonl=output_jsonl,
        receipt_json=receipt_json,
    )
    typer.echo(
        f"schema={receipt['schema']!r} builder={receipt['builder_version']!r} "
        f"sources={receipt['source_records']} groups={receipt['source_groups']}"
    )
    typer.echo(
        "splits=" + json.dumps(receipt["splits"], sort_keys=True)
        + f" corpus_hash={receipt['corpus_hash']}"
    )
    typer.echo(
        f"receipt_hash={receipt['receipt_hash']} "
        f"output={str(output_jsonl)!r} receipt={str(receipt_json)!r}"
    )


@app.command()
def verify_synthetic_corpus_build(
        manifest_jsonl: Path = typer.Option(
            ..., "--manifest-jsonl", exists=True, dir_okay=False, readable=True,
            help="Exact source-record manifest used for the build.",
        ),
        source_root: Path = typer.Option(
            ..., "--source-root", exists=True, file_okay=False, readable=True,
            help="Root containing the preserved source text files.",
        ),
        corpus_jsonl: Path = typer.Option(
            ..., "--corpus-jsonl", exists=True, dir_okay=False, readable=True,
            help="Built calibration corpus to reconstruct and verify.",
        ),
        receipt_json: Path = typer.Option(
            ..., "--receipt-json", exists=True, dir_okay=False, readable=True,
            help="Self-verifying build receipt.",
        ),
) -> None:
    """Reconstruct and verify a corpus build without changing files."""

    receipt = verify_corpus_build(
        manifest_jsonl,
        source_root=source_root,
        corpus_path=corpus_jsonl,
        receipt_path=receipt_json,
    )
    typer.echo(
        f"verified=true schema={receipt['schema']!r} "
        f"sources={receipt['source_records']} groups={receipt['source_groups']}"
    )
    typer.echo(
        f"corpus_hash={receipt['corpus_hash']} "
        f"receipt_hash={receipt['receipt_hash']}"
    )


@app.command()
def validate_synthetic_corpus(
        input_jsonl: Path = typer.Option(
            ...,
            "--input-jsonl",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Immutable JSONL corpus to validate and fingerprint.",
        ),
) -> None:
    """Validate labels, provenance, split isolation and corpus identity."""

    summary = corpus_summary(load_calibration_corpus(input_jsonl))
    typer.echo(
        f"schema={summary['schema']!r} corpus_hash={summary['corpus_hash']} "
        f"samples={summary['samples']} eligible={summary['eligible_samples']}"
    )
    typer.echo(
        "labels=" + json.dumps(summary["labels"], sort_keys=True)
        + " splits=" + json.dumps(summary["splits"], sort_keys=True)
    )
    typer.echo(
        "languages=" + json.dumps(summary["languages"], sort_keys=True)
        + " genres=" + json.dumps(summary["genres"], sort_keys=True)
    )
    typer.echo(
        "split_hashes="
        + json.dumps(summary["split_hashes"], sort_keys=True)
    )


@app.command()
def calibrate_synthetic_origin(
        input_jsonl: Path = typer.Option(
            ...,
            "--input-jsonl",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated JSONL corpus with an isolated calibration split.",
        ),
        output_json: Path = typer.Option(
            ...,
            "--output-json",
            dir_okay=False,
            help="New canonical threshold-decision JSON file.",
        ),
) -> None:
    """Select and bind a threshold using only the calibration split."""

    if output_json.exists():
        raise typer.BadParameter("output-json already exists.")
    decision = calibrate_threshold(
        load_calibration_corpus(input_jsonl),
        software_version=resolve_software_provenance().software_version,
    )
    write_canonical_json(output_json, decision)
    typer.echo(
        f"schema={decision['schema']!r} method={decision['method']!r} "
        f"method_version={decision['method_version']!r} "
        f"threshold={decision['threshold']} "
        f"decision_hash={decision['decision_hash']}"
    )
    typer.echo(f"output={str(output_json)!r}")


@app.command()
def evaluate_synthetic_origin(
        input_jsonl: Path = typer.Option(
            ...,
            "--input-jsonl",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Exact JSONL corpus bound by the threshold decision.",
        ),
        threshold_json: Path = typer.Option(
            ...,
            "--threshold-json",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Hash-verified threshold selected on calibration only.",
        ),
        output_json: Path = typer.Option(
            ...,
            "--output-json",
            dir_okay=False,
            help="New canonical held-out evaluation report JSON file.",
        ),
) -> None:
    """Measure held-out errors without changing the selected threshold."""

    if output_json.exists():
        raise typer.BadParameter("output-json already exists.")
    try:
        decision = json.loads(threshold_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise typer.BadParameter("threshold-json must be valid JSON.") from error
    if not isinstance(decision, dict):
        raise typer.BadParameter("threshold-json must contain one JSON object.")
    report = evaluate_test_split(
        load_calibration_corpus(input_jsonl),
        decision,
        software_version=resolve_software_provenance().software_version,
    )
    write_canonical_json(output_json, report)
    metrics = report["overall"]
    fpr = metrics["false_positive_rate"]
    fnr = metrics["false_negative_rate"]
    typer.echo(
        f"schema={report['schema']!r} method={report['method']!r} "
        f"method_version={report['method_version']!r} "
        f"threshold={report['threshold']} samples={metrics['samples']}"
    )
    typer.echo(
        f"roc_auc={metrics['roc_auc']} "
        f"balanced_accuracy={metrics['balanced_accuracy']} "
        f"false_positive_rate={fpr['rate']} "
        f"false_negative_rate={fnr['rate']} "
        f"sufficient={str(metrics['sufficient_sample_size']).lower()}"
    )
    typer.echo(
        f"test_split_hash={report['test_split_hash']} "
        f"report_hash={report['report_hash']}"
    )
    typer.echo(f"output={str(output_json)!r}")


@app.command()
def recover_analysis(
        analysis_run_id: int = typer.Option(
            ...,
            "--analysis-run-id",
            min=1,
            help="Running analysis whose stale attempt should be abandoned.",
        ),
        stale_after_minutes: int = typer.Option(
            60,
            "--stale-after-minutes",
            min=1,
            help="Minimum age of the running attempt.",
        ),
        operator: str = typer.Option(
            ...,
            "--operator",
            help="Person or operational identity authorizing recovery.",
        ),
        reason: str = typer.Option(
            ...,
            "--reason",
            help="Evidence-based reason the attempt is considered abandoned.",
        ),
) -> None:
    """Abandon one stale running attempt while preserving its audit trail."""

    result = recover_stale_analysis_run(
        analysis_run_id=analysis_run_id,
        stale_after_minutes=stale_after_minutes,
        operator=operator,
        reason=reason,
    )
    typer.echo(
        f"analysis_run_id={result.analysis_run_id} "
        f"attempt={result.attempt_number} "
        f"status={result.status.value} recovered=true "
        f"operator={result.operator!r} reason={result.reason!r}"
    )


@app.command()
def analysis_attempts(
        analysis_run_id: int = typer.Option(
            ...,
            "--analysis-run-id",
            min=1,
            help="Analysis run whose attempt history should be shown.",
        ),
) -> None:
    """Read the ordered immutable execution-attempt audit trail."""

    history = get_analysis_attempt_history(
        analysis_run_id=analysis_run_id,
    )
    typer.echo(
        f"analysis_run_id={history.analysis_run_id} "
        f"status={history.status.value} attempts={history.attempt_count} "
        f"shown={len(history.attempts)}"
    )
    for attempt in history.attempts:
        finished_at = (
            attempt.finished_at.isoformat()
            if attempt.finished_at is not None else None
        )
        typer.echo(
            f"attempt={attempt.attempt_number} "
            f"status={attempt.status.value} "
            f"started_at={attempt.started_at.isoformat()!r} "
            f"finished_at={finished_at!r} "
            f"migrated={str(attempt.migrated).lower()} "
            f"operator={attempt.recovery_operator!r} "
            f"reason={attempt.recovery_reason!r} error={attempt.error!r}"
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
