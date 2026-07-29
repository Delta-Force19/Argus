from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from argus.acquisition import (
    Connector,
    RawArtifactStore,
    RetrievalOutcome,
)
from argus.documents import DocumentType
from argus.extraction import TextExtractor
from argus.models import AcquisitionCandidate, CollectionEndpoint
from argus.services.acquisition_diagnostics import (
    AcquisitionStage,
    AcquisitionStageError,
)
from argus.services.acquisition_pipeline_service import (
    AcquisitionPipelineResult,
    AcquisitionPipelineService,
)


class AcquisitionBatchItemStatus(str, Enum):
    """Final state of one independently executed batch item."""

    PROCESSED = "processed"
    RETRIEVAL_ONLY = "retrieval_only"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AcquisitionBatchItem:
    """Stable inputs needed to acquire one persisted candidate."""

    endpoint_id: int
    candidate_id: int
    connector: Connector
    document_type: DocumentType
    request_metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.endpoint_id < 1:
            raise ValueError("endpoint_id must be greater than zero.")
        if self.candidate_id < 1:
            raise ValueError("candidate_id must be greater than zero.")


@dataclass(frozen=True, slots=True)
class AcquisitionBatchItemResult:
    """Serializable outcome retained after the item's session is closed."""

    candidate_id: int
    status: AcquisitionBatchItemStatus
    url: str | None = None
    failure_stage: AcquisitionStage | None = None
    retrieval_attempt_id: int | None = None
    retrieval_outcome: RetrievalOutcome | None = None
    document_version_id: int | None = None
    derived_artifact_id: int | None = None
    article_id: int | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionBatchReport:
    """Aggregate and per-item results for one batch execution."""

    items: tuple[AcquisitionBatchItemResult, ...]

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def processed_count(self) -> int:
        return self._count(AcquisitionBatchItemStatus.PROCESSED)

    @property
    def retrieval_only_count(self) -> int:
        return self._count(AcquisitionBatchItemStatus.RETRIEVAL_ONLY)

    @property
    def failed_count(self) -> int:
        return self._count(AcquisitionBatchItemStatus.FAILED)

    def _count(self, status: AcquisitionBatchItemStatus) -> int:
        return sum(item.status is status for item in self.items)


class AcquisitionBatchRunner:
    """Run acquisition items in isolated caller-owned transactions.

    A fresh session is opened for every item. Completed pipeline results are
    committed independently; exceptions roll back only the current item and
    are reported without stopping later candidates.
    """

    def __init__(
            self,
            session_factory: Callable[[], Session],
            *,
            artifact_store: RawArtifactStore,
            extractor: TextExtractor,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_store = artifact_store
        self._extractor = extractor

    def run(
            self,
            items: Iterable[AcquisitionBatchItem],
    ) -> AcquisitionBatchReport:
        results = tuple(self._run_item(item) for item in items)
        return AcquisitionBatchReport(items=results)

    def _run_item(
            self,
            item: AcquisitionBatchItem,
    ) -> AcquisitionBatchItemResult:
        session = self._session_factory()
        url = None
        current_stage = AcquisitionStage.PREPARATION

        try:
            endpoint = session.get(CollectionEndpoint, item.endpoint_id)
            if endpoint is None:
                raise LookupError(
                    f"Collection endpoint {item.endpoint_id} does not exist."
                )

            candidate = session.get(
                AcquisitionCandidate,
                item.candidate_id,
            )
            if candidate is None:
                raise LookupError(
                    f"Acquisition candidate {item.candidate_id} "
                    "does not exist."
                )
            url = candidate.location

            pipeline = AcquisitionPipelineService(
                session,
                artifact_store=self._artifact_store,
                extractor=self._extractor,
            )
            result = pipeline.acquire(
                endpoint=endpoint,
                candidate=candidate,
                connector=item.connector,
                document_type=item.document_type,
                request_metadata=item.request_metadata,
            )
            item_result = self._completed_result(
                candidate_id=item.candidate_id,
                url=url,
                result=result,
            )
            current_stage = AcquisitionStage.COMMIT
            session.commit()
            return item_result
        except AcquisitionStageError as error:
            session.rollback()
            original_error = error.original_error
            return AcquisitionBatchItemResult(
                candidate_id=item.candidate_id,
                url=url,
                status=AcquisitionBatchItemStatus.FAILED,
                failure_stage=error.stage,
                error_type=type(original_error).__name__,
                error_message=str(original_error),
            )
        except Exception as error:
            session.rollback()
            return AcquisitionBatchItemResult(
                candidate_id=item.candidate_id,
                url=url,
                status=AcquisitionBatchItemStatus.FAILED,
                failure_stage=current_stage,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        finally:
            session.close()

    @staticmethod
    def _completed_result(
            *,
            candidate_id: int,
            url: str,
            result: AcquisitionPipelineResult,
    ) -> AcquisitionBatchItemResult:
        attempt = result.retrieval_attempt
        processing = result.processing

        if processing is None:
            return AcquisitionBatchItemResult(
                candidate_id=candidate_id,
                url=url,
                status=AcquisitionBatchItemStatus.RETRIEVAL_ONLY,
                failure_stage=AcquisitionStage.RETRIEVAL,
                retrieval_attempt_id=attempt.id,
                retrieval_outcome=attempt.outcome,
                error_message=attempt.error,
            )

        article = processing.projected_article
        return AcquisitionBatchItemResult(
            candidate_id=candidate_id,
            url=url,
            status=AcquisitionBatchItemStatus.PROCESSED,
            retrieval_attempt_id=attempt.id,
            retrieval_outcome=attempt.outcome,
            document_version_id=processing.ingestion.version.id,
            derived_artifact_id=processing.extracted_text.id,
            article_id=article.id if article is not None else None,
        )
