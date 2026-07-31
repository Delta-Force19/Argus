from datetime import datetime, timezone
import json
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
from argus.services.analysis_run_service import prepare_analysis_run
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
            help="Manual outcome: assigned or revoked.",
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
    """Create, link or revoke an explicit candidate identity."""

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
        f"entity_id={result.entity_id} "
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
        f"candidate_shown={len(report.candidate_items)}"
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
            f"{item.assigned_by_candidate_resolution_decision_id or 'none'}"
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
            f"safe_resolved={item.safe_resolved_count}"
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
        f"occurrences={bundle.entities.resolved_occurrence_count}"
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
        software_version: str = typer.Option(
            ...,
            "--software-version",
            help="Exact Argus/software build identifier.",
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
        software_version=software_version,
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
        f"occurrences={result.resolved_occurrence_count}"
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
