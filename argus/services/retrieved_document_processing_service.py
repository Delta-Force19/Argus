from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.acquisition import RawArtifactStore
from argus.documents import DocumentIngestionConflict, DocumentType
from argus.extraction import TextExtractor
from argus.models import (
    AcquisitionCandidate,
    Article,
    DerivedArtifact,
    RetrievalAttempt,
)
from argus.services.article_text_projection_service import (
    ArticleTextProjectionService,
)
from argus.services.document_ingestion_service import (
    DocumentIngestionResult,
    DocumentIngestionService,
)
from argus.services.text_extraction_service import TextExtractionService


@dataclass(frozen=True, slots=True)
class RetrievedDocumentProcessingResult:
    """Normalized outputs produced from one successful retrieval."""

    ingestion: DocumentIngestionResult
    extracted_text: DerivedArtifact
    projected_article: Article | None


class RetrievedDocumentProcessingService:
    """Normalize one retrieved document without another network request.

    The service composes ingestion, text extraction and the temporary legacy
    article projection. It never commits; the caller owns the transaction.
    """

    def __init__(
            self,
            session: Session,
            *,
            artifact_store: RawArtifactStore,
            extractor: TextExtractor,
    ) -> None:
        self._session = session
        self._ingestion_service = DocumentIngestionService(session)
        self._extraction_service = TextExtractionService(
            session,
            artifact_store=artifact_store,
            extractor=extractor,
        )
        self._projection_service = ArticleTextProjectionService(session)

    def process(
            self,
            *,
            attempt: RetrievalAttempt,
            candidate: AcquisitionCandidate,
            document_type: DocumentType,
    ) -> RetrievedDocumentProcessingResult:
        ingestion = self._ingestion_service.ingest_retrieval(
            attempt=attempt,
            candidate=candidate,
            document_type=document_type,
        )
        extracted_text = self._extraction_service.extract(
            ingestion.version
        )
        projected_article = self._project_legacy_article(
            candidate=candidate,
            artifact=extracted_text,
        )
        return RetrievedDocumentProcessingResult(
            ingestion=ingestion,
            extracted_text=extracted_text,
            projected_article=projected_article,
        )

    def _project_legacy_article(
            self,
            *,
            candidate: AcquisitionCandidate,
            artifact: DerivedArtifact,
    ) -> Article | None:
        if candidate.article_id is None:
            return None

        article = self._session.get(Article, candidate.article_id)
        if article is None:
            raise DocumentIngestionConflict(
                "Candidate references a legacy article that does not exist."
            )

        return self._projection_service.project(
            article=article,
            artifact=artifact,
        )
