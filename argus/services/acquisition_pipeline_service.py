from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.acquisition import (
    Connector,
    RawArtifactStore,
    RetrievalOutcome,
)
from argus.documents import DocumentType
from argus.extraction import TextExtractor
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
    RetrievalAttempt,
)
from argus.services.acquisition_diagnostics import (
    AcquisitionStage,
    AcquisitionStageError,
)
from argus.services.retrieval_service import RetrievalService
from argus.services.retrieved_document_processing_service import (
    RetrievedDocumentProcessingResult,
    RetrievedDocumentProcessingService,
)


@dataclass(frozen=True, slots=True)
class AcquisitionPipelineResult:
    """Outputs produced while acquiring one persisted candidate."""

    retrieval_attempt: RetrievalAttempt
    processing: RetrievedDocumentProcessingResult | None


class AcquisitionPipelineService:
    """Retrieve and normalize one persisted acquisition candidate.

    Every connector outcome is recorded as a retrieval attempt. Successful
    retrievals continue through document ingestion, stored-byte extraction
    and optional legacy article projection. Unsuccessful outcomes stop before
    normalization. The service never commits; the caller owns the transaction.
    """

    def __init__(
            self,
            session: Session,
            *,
            artifact_store: RawArtifactStore,
            extractor: TextExtractor,
    ) -> None:
        self._retrieval_service = RetrievalService(
            session=session,
            artifact_store=artifact_store,
        )
        self._processing_service = RetrievedDocumentProcessingService(
            session,
            artifact_store=artifact_store,
            extractor=extractor,
        )

    def acquire(
            self,
            *,
            endpoint: CollectionEndpoint,
            candidate: AcquisitionCandidate,
            connector: Connector,
            document_type: DocumentType,
            request_metadata: Mapping[str, object] | None = None,
    ) -> AcquisitionPipelineResult:
        try:
            attempt = self._retrieval_service.retrieve_candidate(
                endpoint=endpoint,
                candidate=candidate,
                connector=connector,
                request_metadata=request_metadata,
            )
        except Exception as error:
            raise AcquisitionStageError(
                AcquisitionStage.RETRIEVAL,
                error,
            ) from error

        if attempt.outcome is not RetrievalOutcome.SUCCEEDED:
            return AcquisitionPipelineResult(
                retrieval_attempt=attempt,
                processing=None,
            )

        try:
            processing = self._processing_service.process(
                attempt=attempt,
                candidate=candidate,
                document_type=document_type,
            )
        except Exception as error:
            raise AcquisitionStageError(
                AcquisitionStage.PROCESSING,
                error,
            ) from error
        return AcquisitionPipelineResult(
            retrieval_attempt=attempt,
            processing=processing,
        )
