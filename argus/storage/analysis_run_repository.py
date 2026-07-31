from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.analysis_runs import AnalysisRunStatus
from argus.models import AnalysisRun
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
