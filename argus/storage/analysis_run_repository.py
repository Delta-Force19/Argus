from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from argus.analysis_runs import AnalysisAttemptStatus, AnalysisRunStatus
from argus.models import (
    AnalysisEvidence,
    AnalysisExecutionAttempt,
    AnalysisResult,
    AnalysisRun,
)
from argus.storage.base_repository import BaseRepository


class AnalysisRunRepository(BaseRepository[AnalysisRun]):
    """Persist immutable, idempotent analysis-run preparations."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=AnalysisRun)

    def get_reproducible_preparation(
            self,
            *,
            input_fingerprint: str,
            analysis_method: str,
            analysis_method_version: str,
            software_version: str,
            configuration_hash: str,
    ) -> AnalysisRun | None:
        statement = select(AnalysisRun).where(
            AnalysisRun.input_fingerprint == input_fingerprint,
            AnalysisRun.analysis_method == analysis_method,
            AnalysisRun.analysis_method_version
            == analysis_method_version,
            AnalysisRun.software_version == software_version,
            AnalysisRun.configuration_hash == configuration_hash,
        )
        return self.session.scalar(statement)

    def create_prepared(
            self,
            *,
            document_version_id: int,
            entity_type_scope: str,
            analysis_method: str,
            analysis_method_version: str,
            software_version: str,
            configuration: dict[str, object],
            configuration_hash: str,
            input_schema_version: str,
            input_manifest: dict[str, object],
            input_fingerprint: str,
    ) -> AnalysisRun:
        row = AnalysisRun(
            document_version_id=document_version_id,
            entity_type_scope=entity_type_scope,
            analysis_method=analysis_method,
            analysis_method_version=analysis_method_version,
            software_version=software_version,
            configuration=configuration,
            configuration_hash=configuration_hash,
            input_schema_version=input_schema_version,
            input_manifest=input_manifest,
            input_fingerprint=input_fingerprint,
            status=AnalysisRunStatus.PREPARED,
        )
        self.add(row)
        self.flush()
        return row

    def claim_execution(
            self,
            run: AnalysisRun,
            *,
            started_at: datetime,
            retry_failed: bool,
    ) -> bool:
        """Atomically claim one prepared or explicitly retried run."""

        attempt_number = run.attempt_count + 1
        allowed = [AnalysisRunStatus.PREPARED]
        if retry_failed:
            allowed.append(AnalysisRunStatus.FAILED)
        statement = (
            update(AnalysisRun)
            .where(
                AnalysisRun.id == run.id,
                AnalysisRun.status.in_(allowed),
            )
            .values(
                status=AnalysisRunStatus.RUNNING,
                attempt_count=AnalysisRun.attempt_count + 1,
                last_error=None,
                started_at=started_at,
                finished_at=None,
            )
        )
        result = self.session.execute(statement)
        self.session.flush()
        if result.rowcount != 1:
            return False
        self.session.add(AnalysisExecutionAttempt(
            analysis_run_id=run.id,
            attempt_number=attempt_number,
            status=AnalysisAttemptStatus.RUNNING,
            started_at=started_at,
        ))
        self.session.flush()
        return True

    def get_current_attempt(
            self,
            run: AnalysisRun,
    ) -> AnalysisExecutionAttempt | None:
        if run.attempt_count < 1:
            return None
        return self.session.scalar(
            select(AnalysisExecutionAttempt).where(
                AnalysisExecutionAttempt.analysis_run_id == run.id,
                AnalysisExecutionAttempt.attempt_number
                == run.attempt_count,
            )
        )

    def list_attempts(
            self,
            analysis_run_id: int,
    ) -> list[AnalysisExecutionAttempt]:
        return list(self.session.scalars(
            select(AnalysisExecutionAttempt)
            .where(
                AnalysisExecutionAttempt.analysis_run_id == analysis_run_id
            )
            .order_by(AnalysisExecutionAttempt.attempt_number.asc())
        ))

    def mark_completed(
            self,
            run: AnalysisRun,
            *,
            finished_at: datetime,
    ) -> None:
        if run.status is not AnalysisRunStatus.RUNNING:
            raise ValueError("Only a running analysis can complete.")
        run.status = AnalysisRunStatus.COMPLETED
        run.finished_at = finished_at
        run.last_error = None
        attempt = self.get_current_attempt(run)
        if attempt is None or attempt.status is not AnalysisAttemptStatus.RUNNING:
            raise ValueError("Running analysis attempt is missing.")
        attempt.status = AnalysisAttemptStatus.COMPLETED
        attempt.finished_at = finished_at
        self.flush()

    def mark_failed(
            self,
            run: AnalysisRun,
            *,
            error: str,
            finished_at: datetime,
    ) -> None:
        if run.status is not AnalysisRunStatus.RUNNING:
            raise ValueError("Only a running analysis can fail.")
        run.status = AnalysisRunStatus.FAILED
        run.finished_at = finished_at
        run.last_error = error[:4000]
        attempt = self.get_current_attempt(run)
        if attempt is None or attempt.status is not AnalysisAttemptStatus.RUNNING:
            raise ValueError("Running analysis attempt is missing.")
        attempt.status = AnalysisAttemptStatus.FAILED
        attempt.finished_at = finished_at
        attempt.error = error[:4000]
        self.flush()

    def abandon_running(
            self,
            run: AnalysisRun,
            *,
            finished_at: datetime,
            operator: str,
            reason: str,
    ) -> AnalysisExecutionAttempt:
        if run.status is not AnalysisRunStatus.RUNNING:
            raise ValueError("Only a running analysis can be recovered.")
        attempt = self.get_current_attempt(run)
        if attempt is None or attempt.status is not AnalysisAttemptStatus.RUNNING:
            raise ValueError("Running analysis attempt is missing.")
        diagnostic = f"Abandoned by {operator}: {reason}"[:4000]
        run_update = self.session.execute(
            update(AnalysisRun)
            .where(
                AnalysisRun.id == run.id,
                AnalysisRun.status == AnalysisRunStatus.RUNNING,
                AnalysisRun.attempt_count == run.attempt_count,
            )
            .values(
                status=AnalysisRunStatus.FAILED,
                finished_at=finished_at,
                last_error=diagnostic,
            )
        )
        if run_update.rowcount != 1:
            raise ValueError("Running analysis changed during recovery.")
        attempt_update = self.session.execute(
            update(AnalysisExecutionAttempt)
            .where(
                AnalysisExecutionAttempt.id == attempt.id,
                AnalysisExecutionAttempt.status
                == AnalysisAttemptStatus.RUNNING,
            )
            .values(
                status=AnalysisAttemptStatus.ABANDONED,
                finished_at=finished_at,
                error=diagnostic,
                recovery_operator=operator,
                recovery_reason=reason,
            )
        )
        if attempt_update.rowcount != 1:
            raise ValueError("Running attempt changed during recovery.")
        self.flush()
        return attempt

    def get_result(self, analysis_run_id: int) -> AnalysisResult | None:
        return self.session.scalar(
            select(AnalysisResult).where(
                AnalysisResult.analysis_run_id == analysis_run_id
            )
        )

    def create_result(
            self,
            *,
            analysis_run_id: int,
            result_schema_version: str,
            payload: dict[str, object],
            warnings: list[str],
            output_hash: str,
            evidence_set_hash: str,
    ) -> AnalysisResult:
        row = AnalysisResult(
            analysis_run_id=analysis_run_id,
            result_schema_version=result_schema_version,
            payload=payload,
            warnings=warnings,
            output_hash=output_hash,
            evidence_set_hash=evidence_set_hash,
        )
        self.session.add(row)
        self.flush()
        return row

    def create_evidence(
            self,
            *,
            analysis_result_id: int,
            evidence_index: int,
            evidence_schema_version: str,
            category: str,
            modality: str,
            locator: dict[str, object],
            payload: dict[str, object],
            evidence_hash: str,
    ) -> AnalysisEvidence:
        row = AnalysisEvidence(
            analysis_result_id=analysis_result_id,
            evidence_index=evidence_index,
            evidence_schema_version=evidence_schema_version,
            category=category,
            modality=modality,
            locator=locator,
            payload=payload,
            evidence_hash=evidence_hash,
        )
        self.session.add(row)
        self.flush()
        return row

    def list_evidence(
            self,
            analysis_result_id: int,
    ) -> list[AnalysisEvidence]:
        return list(self.session.scalars(
            select(AnalysisEvidence)
            .where(
                AnalysisEvidence.analysis_result_id == analysis_result_id
            )
            .order_by(AnalysisEvidence.evidence_index.asc())
        ))
