from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from argus.analysis_runs import AnalysisRunStatus
from argus.models import AnalysisResult, AnalysisRun
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
        return result.rowcount == 1

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
        self.flush()

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
    ) -> AnalysisResult:
        row = AnalysisResult(
            analysis_run_id=analysis_run_id,
            result_schema_version=result_schema_version,
            payload=payload,
            warnings=warnings,
            output_hash=output_hash,
        )
        self.session.add(row)
        self.flush()
        return row
