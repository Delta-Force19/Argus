from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy.orm import Session

from argus.analysis.methods import (
    AnalysisMethodRegistry,
    AnalysisMethodEvidence,
    AnalysisMethodOutput,
    default_analysis_method_registry,
)
from argus.analysis_runs import AnalysisAttemptStatus, AnalysisRunStatus
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.models import (
    AnalysisEvidence,
    AnalysisResult,
    AnalysisRun,
    DerivedArtifact,
)
from argus.services.software_provenance_service import (
    resolve_software_provenance,
)
from argus.storage.analysis_run_repository import AnalysisRunRepository


@dataclass(frozen=True, slots=True)
class ExecutedAnalysisRun:
    """Detached result identity and terminal state of one execution."""

    analysis_run_id: int
    analysis_result_id: int
    executed: bool
    status: AnalysisRunStatus
    attempt_count: int
    analysis_method: str
    analysis_method_version: str
    software_version: str
    result_schema_version: str
    output_hash: str
    warning_count: int
    evidence_count: int


@dataclass(frozen=True, slots=True)
class AnalysisRunResultView:
    """Detached, hash-verified analytical output for inspection."""

    analysis_run_id: int
    analysis_result_id: int
    status: AnalysisRunStatus
    attempt_count: int
    analysis_method: str
    analysis_method_version: str
    software_version: str
    result_schema_version: str
    output_hash: str
    evidence_set_hash: str | None
    evidence_count: int
    payload: dict[str, object]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveredAnalysisRun:
    """Detached audit identity for one explicitly abandoned attempt."""

    analysis_run_id: int
    attempt_number: int
    status: AnalysisRunStatus
    operator: str
    reason: str
    started_at: datetime
    recovered_at: datetime


@dataclass(frozen=True, slots=True)
class AnalysisAttemptView:
    """Detached immutable audit row for one execution attempt."""

    attempt_number: int
    status: AnalysisAttemptStatus
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    recovery_operator: str | None
    recovery_reason: str | None
    migrated: bool


@dataclass(frozen=True, slots=True)
class AnalysisAttemptHistory:
    """Run identity and its ordered execution-attempt audit trail."""

    analysis_run_id: int
    status: AnalysisRunStatus
    attempt_count: int
    attempts: tuple[AnalysisAttemptView, ...]


@dataclass(frozen=True, slots=True)
class AnalysisEvidenceView:
    """Detached, hash-verified evidence row with a source locator."""

    evidence_id: int
    evidence_index: int
    evidence_schema_version: str
    category: str
    modality: str
    locator: dict[str, object]
    payload: dict[str, object]
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class AnalysisEvidenceSet:
    """One result identity and its ordered verified evidence set."""

    analysis_run_id: int
    analysis_result_id: int
    evidence_set_hash: str | None
    evidence: tuple[AnalysisEvidenceView, ...]


class AnalysisExecutionFailed(RuntimeError):
    """Raised after a method failure has been persisted on its run."""


def get_analysis_attempt_history(
        *,
        analysis_run_id: int,
        session_factory: Callable[[], Session] = SessionLocal,
) -> AnalysisAttemptHistory:
    """Return the ordered, non-destructive execution audit for one run."""

    if analysis_run_id < 1:
        raise ValueError("analysis_run_id must be greater than zero.")
    with session_factory() as session:
        repository = AnalysisRunRepository(session)
        run = repository.get_by_id(analysis_run_id)
        if run is None:
            raise ValueError(
                f"Analysis run does not exist: {analysis_run_id}."
            )
        rows = repository.list_attempts(run.id)
        if run.attempt_count == 0 and rows:
            raise ValueError("Unclaimed analysis run has attempt records.")
        if run.attempt_count > 0 and (
            not rows or rows[-1].attempt_number != run.attempt_count
        ):
            raise ValueError("Analysis attempt history is incomplete.")
        return AnalysisAttemptHistory(
            analysis_run_id=run.id,
            status=run.status,
            attempt_count=run.attempt_count,
            attempts=tuple(
                AnalysisAttemptView(
                    attempt_number=row.attempt_number,
                    status=row.status,
                    started_at=_as_utc(row.started_at),
                    finished_at=(
                        _as_utc(row.finished_at)
                        if row.finished_at is not None else None
                    ),
                    error=row.error,
                    recovery_operator=row.recovery_operator,
                    recovery_reason=row.recovery_reason,
                    migrated=row.migrated,
                )
                for row in rows
            ),
        )


def recover_stale_analysis_run(
        *,
        analysis_run_id: int,
        stale_after_minutes: int,
        operator: str,
        reason: str,
        now: datetime | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> RecoveredAnalysisRun:
    """Explicitly abandon one stale running attempt for audited retry."""

    if analysis_run_id < 1:
        raise ValueError("analysis_run_id must be greater than zero.")
    if stale_after_minutes < 1:
        raise ValueError("stale_after_minutes must be greater than zero.")
    normalized_operator = operator.strip()
    normalized_reason = reason.strip()
    if not normalized_operator or len(normalized_operator) > 255:
        raise ValueError("operator must contain 1 to 255 characters.")
    if not normalized_reason or len(normalized_reason) > 4000:
        raise ValueError("reason must contain 1 to 4000 characters.")
    recovered_at = _as_utc(now or _utc_now())
    cutoff = recovered_at - timedelta(minutes=stale_after_minutes)

    with session_factory() as session:
        repository = AnalysisRunRepository(session)
        run = repository.get_by_id(analysis_run_id)
        if run is None:
            raise ValueError(
                f"Analysis run does not exist: {analysis_run_id}."
            )
        if run.status is not AnalysisRunStatus.RUNNING:
            raise ValueError(
                "Only a running analysis run can be recovered."
            )
        if repository.get_result(run.id) is not None:
            raise ValueError("Running analysis run already has a result.")
        if run.started_at is None:
            raise ValueError("Running analysis run has no start time.")
        started_at = _as_utc(run.started_at)
        if started_at > cutoff:
            raise ValueError(
                "Analysis run is not stale: its current attempt is newer "
                "than the requested threshold."
            )
        attempt = repository.abandon_running(
            run,
            finished_at=recovered_at,
            operator=normalized_operator,
            reason=normalized_reason,
        )
        detached = RecoveredAnalysisRun(
            analysis_run_id=run.id,
            attempt_number=attempt.attempt_number,
            status=run.status,
            operator=normalized_operator,
            reason=normalized_reason,
            started_at=started_at,
            recovered_at=recovered_at,
        )
        session.commit()
        return detached


def execute_analysis_run(
        *,
        analysis_run_id: int,
        retry_failed: bool = False,
        registry: AnalysisMethodRegistry | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> ExecutedAnalysisRun:
    """Claim, execute and atomically persist one reproducible output."""

    if analysis_run_id < 1:
        raise ValueError("analysis_run_id must be greater than zero.")
    method_registry = registry or default_analysis_method_registry()

    with session_factory() as session:
        repository = AnalysisRunRepository(session)
        run = repository.get_by_id(analysis_run_id)
        if run is None:
            raise ValueError(
                f"Analysis run does not exist: {analysis_run_id}."
            )
        _validate_persisted_run_integrity(run)
        existing = repository.get_result(run.id)
        if run.status is AnalysisRunStatus.COMPLETED:
            text = _load_verified_text(session, run)
            return _completed_result(
                run,
                existing,
                repository=repository,
                text=text,
                executed=False,
            )
        if existing is not None:
            raise ValueError(
                "Non-completed analysis run already has a result."
            )
        software_version = resolve_software_provenance().software_version
        if run.software_version != software_version:
            raise ValueError(
                "Analysis run software provenance does not match the "
                "currently executing Argus code."
            )
        method = method_registry.require(
            run.analysis_method,
            run.analysis_method_version,
        )
        text = _load_verified_text(session, run)
        if run.status is AnalysisRunStatus.RUNNING:
            raise ValueError("Analysis run is already running.")
        if (
            run.status is AnalysisRunStatus.FAILED
            and not retry_failed
        ):
            raise ValueError(
                "Analysis run failed previously; pass retry_failed=True "
                "to retry the same prepared contract."
            )
        claimed = repository.claim_execution(
            run,
            started_at=_utc_now(),
            retry_failed=retry_failed,
        )
        if not claimed:
            session.rollback()
            raise ValueError("Analysis run could not be claimed.")
        session.commit()
        manifest = _json_object(run.input_manifest, field="input_manifest")
        configuration = _json_object(
            run.configuration,
            field="configuration",
        )

    try:
        output = method.execute(
            text=text,
            input_manifest=manifest,
            configuration=configuration,
        )
        normalized = _normalize_output(output)
        return _persist_success(
            analysis_run_id=analysis_run_id,
            output=normalized,
            session_factory=session_factory,
        )
    except Exception as error:
        _persist_failure(
            analysis_run_id=analysis_run_id,
            error=error,
            session_factory=session_factory,
        )
        raise AnalysisExecutionFailed(
            f"Analysis run {analysis_run_id} failed: {error}"
        ) from error


def get_analysis_run_result(
        *,
        analysis_run_id: int,
        session_factory: Callable[[], Session] = SessionLocal,
) -> AnalysisRunResultView:
    """Return one completed result after verifying its content hash."""

    if analysis_run_id < 1:
        raise ValueError("analysis_run_id must be greater than zero.")
    with session_factory() as session:
        repository = AnalysisRunRepository(session)
        run = repository.get_by_id(analysis_run_id)
        if run is None:
            raise ValueError(
                f"Analysis run does not exist: {analysis_run_id}."
            )
        if run.status is not AnalysisRunStatus.COMPLETED:
            raise ValueError(
                "Analysis result is unavailable: "
                f"run status is {run.status.value}."
            )
        _validate_persisted_run_integrity(run)
        result = repository.get_result(run.id)
        text = _load_verified_text(session, run)
        completed = _completed_result(
            run,
            result,
            repository=repository,
            text=text,
            executed=False,
        )
        return AnalysisRunResultView(
            analysis_run_id=completed.analysis_run_id,
            analysis_result_id=completed.analysis_result_id,
            status=completed.status,
            attempt_count=completed.attempt_count,
            analysis_method=completed.analysis_method,
            analysis_method_version=completed.analysis_method_version,
            software_version=completed.software_version,
            result_schema_version=completed.result_schema_version,
            output_hash=completed.output_hash,
            evidence_set_hash=result.evidence_set_hash,
            evidence_count=completed.evidence_count,
            payload=_json_object(result.payload, field="result payload"),
            warnings=tuple(_warnings(result.warnings)),
        )


def get_analysis_evidence(
        *,
        analysis_run_id: int,
        session_factory: Callable[[], Session] = SessionLocal,
) -> AnalysisEvidenceSet:
    """Return ordered evidence after verifying hashes and source locators."""

    if analysis_run_id < 1:
        raise ValueError("analysis_run_id must be greater than zero.")
    with session_factory() as session:
        repository = AnalysisRunRepository(session)
        run = repository.get_by_id(analysis_run_id)
        if run is None:
            raise ValueError(
                f"Analysis run does not exist: {analysis_run_id}."
            )
        if run.status is not AnalysisRunStatus.COMPLETED:
            raise ValueError(
                "Analysis evidence is unavailable: "
                f"run status is {run.status.value}."
            )
        _validate_persisted_run_integrity(run)
        text = _load_verified_text(session, run)
        result = repository.get_result(run.id)
        completed = _completed_result(
            run,
            result,
            repository=repository,
            text=text,
            executed=False,
        )
        rows = repository.list_evidence(completed.analysis_result_id)
        return AnalysisEvidenceSet(
            analysis_run_id=run.id,
            analysis_result_id=completed.analysis_result_id,
            evidence_set_hash=result.evidence_set_hash,
            evidence=tuple(_evidence_view(row) for row in rows),
        )


def _persist_success(
        *,
        analysis_run_id: int,
        output: AnalysisMethodOutput,
        session_factory: Callable[[], Session],
) -> ExecutedAnalysisRun:
    payload = _json_object(output.payload, field="result payload")
    warnings = _warnings(output.warnings)
    evidence = list(output.evidence)
    evidence_hashes = [_method_evidence_hash(item) for item in evidence]
    evidence_set_hash = _json_hash(evidence_hashes)
    result_hash = _json_hash({
        "result_schema_version": output.result_schema_version,
        "payload": payload,
        "warnings": warnings,
        "evidence_set_hash": evidence_set_hash,
    })
    with session_factory() as session:
        try:
            repository = AnalysisRunRepository(session)
            run = repository.get_by_id(analysis_run_id)
            if run is None:
                raise ValueError("Claimed analysis run disappeared.")
            if run.status is not AnalysisRunStatus.RUNNING:
                raise ValueError(
                    "Claimed analysis run is no longer running."
                )
            if repository.get_result(run.id) is not None:
                raise ValueError("Analysis run already has a result.")
            result = repository.create_result(
                analysis_run_id=run.id,
                result_schema_version=output.result_schema_version,
                payload=payload,
                warnings=warnings,
                output_hash=result_hash,
                evidence_set_hash=evidence_set_hash,
            )
            for index, item in enumerate(evidence):
                repository.create_evidence(
                    analysis_result_id=result.id,
                    evidence_index=index,
                    evidence_schema_version=item.evidence_schema_version,
                    category=item.category,
                    modality=item.modality,
                    locator=_json_object(
                        item.locator,
                        field="evidence locator",
                    ),
                    payload=_json_object(
                        item.payload,
                        field="evidence payload",
                    ),
                    evidence_hash=evidence_hashes[index],
                )
            repository.mark_completed(run, finished_at=_utc_now())
            text = _load_verified_text(session, run)
            detached = _completed_result(
                run,
                result,
                repository=repository,
                text=text,
                executed=True,
            )
            session.commit()
            return detached
        except Exception:
            session.rollback()
            raise


def _persist_failure(
        *,
        analysis_run_id: int,
        error: Exception,
        session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        try:
            repository = AnalysisRunRepository(session)
            run = repository.get_by_id(analysis_run_id)
            if run is None or run.status is not AnalysisRunStatus.RUNNING:
                session.rollback()
                return
            repository.mark_failed(
                run,
                error=f"{type(error).__name__}: {error}",
                finished_at=_utc_now(),
            )
            session.commit()
        except Exception:
            session.rollback()
            raise


def _validate_persisted_run_integrity(run: AnalysisRun) -> None:
    manifest = _json_object(run.input_manifest, field="input_manifest")
    configuration = _json_object(run.configuration, field="configuration")
    if manifest.get("schema_version") != run.input_schema_version:
        raise ValueError("Analysis run input schema is inconsistent.")
    if _json_hash(manifest) != run.input_fingerprint:
        raise ValueError("Analysis run input fingerprint is inconsistent.")
    if _json_hash(configuration) != run.configuration_hash:
        raise ValueError(
            "Analysis run configuration hash is inconsistent."
        )


def _load_verified_text(session: Session, run: AnalysisRun) -> str:
    manifest = _json_object(run.input_manifest, field="input_manifest")
    text_manifest = manifest.get("text")
    if not isinstance(text_manifest, dict):
        raise ValueError("Analysis run text manifest is invalid.")
    artifact_id = text_manifest.get("derived_artifact_id")
    if not isinstance(artifact_id, int) or isinstance(artifact_id, bool):
        raise ValueError("Analysis run text artifact id is invalid.")
    artifact = session.get(DerivedArtifact, artifact_id)
    if artifact is None:
        raise ValueError("Analysis run text artifact does not exist.")
    expected = {
        "artifact_type": artifact.artifact_type.value,
        "method": artifact.method,
        "method_version": artifact.method_version,
        "schema_version": artifact.schema_version,
        "content_hash": artifact.content_hash,
    }
    for field, value in expected.items():
        if text_manifest.get(field) != value:
            raise ValueError(
                f"Analysis run text artifact {field} is inconsistent."
            )
    if artifact.document_version_id != run.document_version_id:
        raise ValueError(
            "Analysis run text artifact belongs to another document version."
        )
    if artifact.artifact_type is not DerivedArtifactType.EXTRACTED_TEXT:
        raise ValueError("Analysis run text artifact has the wrong type.")
    payload = _json_object(artifact.payload, field="text payload")
    if _json_hash(payload) != artifact.content_hash:
        raise ValueError("Analysis run text artifact payload is corrupted.")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Analysis run text is unavailable.")
    if text_manifest.get("character_count") != len(text):
        raise ValueError("Analysis run text character count is inconsistent.")
    return text


def _normalize_output(output: AnalysisMethodOutput) -> AnalysisMethodOutput:
    schema = output.result_schema_version.strip()
    if not schema or len(schema) > 100:
        raise ValueError("result_schema_version is invalid.")
    return AnalysisMethodOutput(
        result_schema_version=schema,
        payload=_json_object(output.payload, field="result payload"),
        warnings=tuple(_warnings(output.warnings)),
        evidence=tuple(
            _normalize_method_evidence(item)
            for item in output.evidence
        ),
    )


def _completed_result(
        run: AnalysisRun,
        result: AnalysisResult | None,
        *,
        repository: AnalysisRunRepository,
        text: str,
        executed: bool,
) -> ExecutedAnalysisRun:
    if result is None:
        raise ValueError("Completed analysis run has no result.")
    if (
        run.status is not AnalysisRunStatus.COMPLETED
        or run.attempt_count < 1
        or run.started_at is None
        or run.finished_at is None
        or run.last_error is not None
    ):
        raise ValueError("Completed analysis run lifecycle is inconsistent.")
    evidence_rows = repository.list_evidence(result.id)
    if result.evidence_set_hash is None:
        if evidence_rows:
            raise ValueError(
                "Legacy analysis result unexpectedly has external evidence."
            )
        expected_hash = _json_hash({
            "result_schema_version": result.result_schema_version,
            "payload": result.payload,
            "warnings": result.warnings,
        })
    else:
        evidence_hashes = _verify_evidence_rows(
            evidence_rows,
            run=run,
            text=text,
        )
        if _json_hash(evidence_hashes) != result.evidence_set_hash:
            raise ValueError("Stored evidence set hash is inconsistent.")
        expected_hash = _json_hash({
            "result_schema_version": result.result_schema_version,
            "payload": result.payload,
            "warnings": result.warnings,
            "evidence_set_hash": result.evidence_set_hash,
        })
    if expected_hash != result.output_hash:
        raise ValueError("Stored analysis result hash is inconsistent.")
    return ExecutedAnalysisRun(
        analysis_run_id=run.id,
        analysis_result_id=result.id,
        executed=executed,
        status=run.status,
        attempt_count=run.attempt_count,
        analysis_method=run.analysis_method,
        analysis_method_version=run.analysis_method_version,
        software_version=run.software_version,
        result_schema_version=result.result_schema_version,
        output_hash=result.output_hash,
        warning_count=len(result.warnings),
        evidence_count=len(evidence_rows),
    )


def _normalize_method_evidence(
        item: AnalysisMethodEvidence,
) -> AnalysisMethodEvidence:
    if not isinstance(item, AnalysisMethodEvidence):
        raise ValueError(
            "evidence must contain AnalysisMethodEvidence values."
        )
    schema = item.evidence_schema_version.strip()
    category = item.category.strip()
    modality = item.modality.strip().lower()
    if not schema or len(schema) > 100:
        raise ValueError("evidence_schema_version is invalid.")
    if not category or len(category) > 100:
        raise ValueError("evidence category is invalid.")
    if modality not in {"text", "image", "audio", "video"}:
        raise ValueError("evidence modality is invalid.")
    if modality != "text":
        raise ValueError(
            "The current document input contract supports only text evidence."
        )
    return AnalysisMethodEvidence(
        evidence_schema_version=schema,
        category=category,
        modality=modality,
        locator=_json_object(item.locator, field="evidence locator"),
        payload=_json_object(item.payload, field="evidence payload"),
    )


def _method_evidence_hash(item: AnalysisMethodEvidence) -> str:
    return _json_hash({
        "evidence_schema_version": item.evidence_schema_version,
        "category": item.category,
        "modality": item.modality,
        "locator": item.locator,
        "payload": item.payload,
    })


def _verify_evidence_rows(
        rows: Sequence[AnalysisEvidence],
        *,
        run: AnalysisRun,
        text: str,
) -> list[str]:
    hashes: list[str] = []
    for expected_index, row in enumerate(rows):
        if row.evidence_index != expected_index:
            raise ValueError("Stored analysis evidence order is incomplete.")
        view = _evidence_view(row)
        expected_hash = _json_hash({
            "evidence_schema_version": view.evidence_schema_version,
            "category": view.category,
            "modality": view.modality,
            "locator": view.locator,
            "payload": view.payload,
        })
        if expected_hash != row.evidence_hash:
            raise ValueError("Stored analysis evidence hash is inconsistent.")
        if view.modality != "text":
            raise ValueError(
                "Current analysis input cannot verify non-text evidence."
            )
        _verify_text_locator(
            locator=view.locator,
            payload=view.payload,
            run=run,
            text=text,
        )
        hashes.append(row.evidence_hash)
    return hashes


def _verify_text_locator(
        *,
        locator: Mapping[str, object],
        payload: Mapping[str, object],
        run: AnalysisRun,
        text: str,
) -> None:
    required = {
        "type",
        "derived_artifact_id",
        "start_char",
        "end_char",
        "content_sha256",
    }
    if set(locator) != required or locator.get("type") != "text_span":
        raise ValueError("Text evidence locator is invalid.")
    manifest = _json_object(run.input_manifest, field="input_manifest")
    text_manifest = manifest.get("text")
    if not isinstance(text_manifest, dict):
        raise ValueError("Analysis run text manifest is invalid.")
    artifact_id = locator.get("derived_artifact_id")
    start = locator.get("start_char")
    end = locator.get("end_char")
    content_hash = locator.get("content_sha256")
    if (
        not isinstance(artifact_id, int)
        or isinstance(artifact_id, bool)
        or artifact_id != text_manifest.get("derived_artifact_id")
    ):
        raise ValueError("Text evidence artifact is inconsistent.")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(text)
    ):
        raise ValueError("Text evidence character range is invalid.")
    excerpt = text[start:end]
    if (
        not isinstance(content_hash, str)
        or len(content_hash) != 64
        or sha256(excerpt.encode("utf-8")).hexdigest() != content_hash
    ):
        raise ValueError("Text evidence content hash is inconsistent.")
    if payload.get("excerpt") != excerpt:
        raise ValueError("Text evidence excerpt is inconsistent.")


def _evidence_view(row: AnalysisEvidence) -> AnalysisEvidenceView:
    return AnalysisEvidenceView(
        evidence_id=row.id,
        evidence_index=row.evidence_index,
        evidence_schema_version=row.evidence_schema_version,
        category=row.category,
        modality=row.modality,
        locator=_json_object(row.locator, field="evidence locator"),
        payload=_json_object(row.payload, field="evidence payload"),
        evidence_hash=row.evidence_hash,
    )


def _json_object(
        value: Mapping[str, object],
        *,
        field: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be one JSON object.")
    normalized = _canonical_json(value, field=field)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field} must be one JSON object.")
    return normalized


def _warnings(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("warnings must be a sequence of strings.")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("warnings must contain non-blank strings.")
        normalized = value.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _json_hash(value: object) -> str:
    canonical = json.dumps(
        _canonical_json(value, field="JSON value"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _canonical_json(value: object, *, field: str) -> object:
    _require_string_keys(value, field=field)
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must contain finite JSON values.") from error
    return json.loads(serialized)


def _require_string_keys(value: object, *, field: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} keys must be strings.")
            _require_string_keys(item, field=field)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_string_keys(item, field=field)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
