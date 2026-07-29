import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import (
    CandidateRecord,
    RetrievalOutcome,
    RetrievalResult,
)
from argus.database import Base
from argus.documents import DocumentType
from argus.endpoints import EndpointType
from argus.extraction import ExtractedText
from argus.models import (
    AcquisitionCandidate,
    Article,
    CollectionEndpoint,
    DerivedArtifact,
    DocumentVersion,
    RetrievalAttempt,
    Source,
)
from argus.services.acquisition_pipeline_service import (
    AcquisitionPipelineService,
)
from argus.services.acquisition_diagnostics import (
    AcquisitionStage,
    AcquisitionStageError,
)
from argus.sources import SourceType
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.candidate_repository import (
    AcquisitionCandidateRepository,
)
from argus.storage.document_repository import DocumentRepository


class StubConnector:
    connector_id = "rss"
    connector_version = "1.0.0"

    def __init__(
            self,
            *,
            outcome: RetrievalOutcome = RetrievalOutcome.SUCCEEDED,
            raises: bool = False,
    ) -> None:
        self.outcome = outcome
        self.raises = raises
        self.calls: list[CandidateRecord] = []

    def discover(self, request):
        raise NotImplementedError

    def retrieve(
            self,
            candidate: CandidateRecord,
    ) -> RetrievalResult:
        self.calls.append(candidate)
        if self.raises:
            raise RuntimeError("retrieval exploded")

        succeeded = self.outcome is RetrievalOutcome.SUCCEEDED
        return RetrievalResult(
            candidate=candidate,
            outcome=self.outcome,
            retrieved_at=datetime(
                2026,
                7,
                26,
                20,
                0,
                tzinfo=timezone.utc,
            ),
            resolved_location=(
                candidate.location if succeeded else None
            ),
            response_status="200" if succeeded else "404",
            content_type=(
                "text/html; charset=utf-8"
                if succeeded
                else None
            ),
            content=(
                b"<html><body>retrieved article</body></html>"
                if succeeded
                else None
            ),
        )


class StubExtractor:
    method = "stub-extractor"
    method_version = "1.0.0"

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[bytes, str | None]] = []

    def extract(
            self,
            content: bytes,
            *,
            media_type: str | None,
    ) -> ExtractedText:
        self.calls.append((content, media_type))
        if self.raises:
            raise ValueError("extraction exploded")

        return ExtractedText(
            text="Normalized retrieved article",
            quality_limitations=("Fixture extractor.",),
        )


class AcquisitionPipelineServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.source = Source(
            identifier="example-news",
            name="Example News",
            source_type=SourceType.NEWS_MEDIA,
            default_language="en",
        )
        self.session.add(self.source)
        self.session.flush()
        self.endpoint = CollectionEndpoint(
            identifier="example-rss",
            source_id=self.source.id,
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
            language="en",
        )
        self.session.add(self.endpoint)
        self.session.flush()
        document = DocumentRepository(
            self.session
        ).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/article",
            document_type=DocumentType.ARTICLE,
            source_id=self.source.id,
            title="Example article",
            language="en",
        )
        self.article = Article(
            document_id=document.id,
            source_id=self.source.id,
            url="https://example.com/article",
            title="Example article",
            language="en",
        )
        self.session.add(self.article)
        self.session.flush()
        candidate_record = CandidateRecord(
            connector_id="rss",
            connector_version="1.0.0",
            location=self.article.url,
            discovered_at=datetime(
                2026,
                7,
                26,
                19,
                0,
                tzinfo=timezone.utc,
            ),
            external_identifier="article-1",
            title=self.article.title,
            source_identifier=self.source.identifier,
            language="en",
        )
        self.candidate = AcquisitionCandidateRepository(
            self.session
        ).get_or_create(
            endpoint=self.endpoint,
            candidate=candidate_record,
            article_id=self.article.id,
        )
        self.extractor = StubExtractor()
        self.service = AcquisitionPipelineService(
            self.session,
            artifact_store=self.store,
            extractor=self.extractor,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_success_runs_retrieval_and_normalization(self) -> None:
        connector = StubConnector()

        result = self.service.acquire(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
            document_type=DocumentType.ARTICLE,
            request_metadata={"trigger": "continuous"},
        )

        self.assertEqual(
            result.retrieval_attempt.outcome,
            RetrievalOutcome.SUCCEEDED,
        )
        self.assertEqual(
            result.retrieval_attempt.request_metadata,
            {"trigger": "continuous"},
        )
        self.assertIsNotNone(result.processing)
        self.assertEqual(
            result.processing.extracted_text.payload["text"],
            "Normalized retrieved article",
        )
        self.assertIs(
            result.processing.projected_article,
            self.article,
        )
        self.assertEqual(
            self.article.content,
            "Normalized retrieved article",
        )
        self.assertEqual(len(connector.calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)

    def test_unsuccessful_retrieval_stops_before_normalization(
            self,
    ) -> None:
        connector = StubConnector(
            outcome=RetrievalOutcome.UNAVAILABLE,
        )

        result = self.service.acquire(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
            document_type=DocumentType.ARTICLE,
        )

        self.assertEqual(
            result.retrieval_attempt.outcome,
            RetrievalOutcome.UNAVAILABLE,
        )
        self.assertIsNone(result.processing)
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DocumentVersion)
            ),
            0,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            ),
            0,
        )

    def test_retrieval_exception_retains_stage_and_original_error(
            self,
    ) -> None:
        with self.assertRaises(AcquisitionStageError) as raised:
            self.service.acquire(
                endpoint=self.endpoint,
                candidate=self.candidate,
                connector=StubConnector(raises=True),
                document_type=DocumentType.ARTICLE,
            )

        self.assertEqual(
            raised.exception.stage,
            AcquisitionStage.RETRIEVAL,
        )
        self.assertIsInstance(
            raised.exception.original_error,
            RuntimeError,
        )
        self.assertEqual(
            str(raised.exception.original_error),
            "retrieval exploded",
        )

    def test_processing_exception_retains_stage_and_original_error(
            self,
    ) -> None:
        service = AcquisitionPipelineService(
            self.session,
            artifact_store=self.store,
            extractor=StubExtractor(raises=True),
        )

        with self.assertRaises(AcquisitionStageError) as raised:
            service.acquire(
                endpoint=self.endpoint,
                candidate=self.candidate,
                connector=StubConnector(),
                document_type=DocumentType.ARTICLE,
            )

        self.assertEqual(
            raised.exception.stage,
            AcquisitionStage.PROCESSING,
        )
        self.assertIsInstance(
            raised.exception.original_error,
            ValueError,
        )
        self.assertEqual(
            str(raised.exception.original_error),
            "extraction exploded",
        )

    def test_repeated_success_keeps_attempts_and_reuses_outputs(
            self,
    ) -> None:
        connector = StubConnector()

        first = self.service.acquire(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
            document_type=DocumentType.ARTICLE,
        )
        second = self.service.acquire(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
            document_type=DocumentType.ARTICLE,
        )

        self.assertNotEqual(
            first.retrieval_attempt.id,
            second.retrieval_attempt.id,
        )
        self.assertIs(
            first.processing.ingestion.version,
            second.processing.ingestion.version,
        )
        self.assertIs(
            first.processing.extracted_text,
            second.processing.extracted_text,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(RetrievalAttempt)
            ),
            2,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DocumentVersion)
            ),
            1,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            ),
            1,
        )

    def test_service_does_not_commit(self) -> None:
        article_id = self.article.id
        candidate_id = self.candidate.id
        endpoint_id = self.endpoint.id
        self.session.commit()

        self.service.acquire(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=StubConnector(),
            document_type=DocumentType.ARTICLE,
        )
        self.session.rollback()

        article = self.session.get(Article, article_id)
        self.assertIsNone(article.content)
        self.assertIsNone(article.content_derived_artifact_id)
        self.assertIsNotNone(
            self.session.get(AcquisitionCandidate, candidate_id)
        )
        self.assertIsNotNone(
            self.session.get(CollectionEndpoint, endpoint_id)
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(RetrievalAttempt)
            ),
            0,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DocumentVersion)
            ),
            0,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
