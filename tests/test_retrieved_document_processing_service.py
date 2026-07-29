import unittest
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import RetrievalOutcome
from argus.database import Base
from argus.documents import ArticleTextProjectionConflict, DocumentType
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
from argus.services.retrieved_document_processing_service import (
    RetrievedDocumentProcessingService,
)
from argus.sources import SourceType
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.document_repository import DocumentRepository
from argus.storage.raw_artifact_repository import RawArtifactRepository


class StubExtractor:
    method = "stub-extractor"
    method_version = "1.0.0"

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str | None]] = []

    def extract(
            self,
            content: bytes,
            *,
            media_type: str | None,
    ) -> ExtractedText:
        self.calls.append((content, media_type))
        return ExtractedText(
            text="Normalized stored document text",
            quality_limitations=("Fixture extractor.",),
        )


class RetrievedDocumentProcessingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.now = datetime(
            2026,
            7,
            26,
            18,
            0,
            tzinfo=timezone.utc,
        )
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
        self.document = DocumentRepository(
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
            document_id=self.document.id,
            source_id=self.source.id,
            url="https://example.com/article",
            title="Example article",
            language="en",
        )
        self.session.add(self.article)
        self.session.flush()
        self.candidate = self._candidate(
            location=self.article.url,
            article_id=self.article.id,
        )
        self.attempt = self._attempt(
            candidate=self.candidate,
            content=b"<html><body>stored source bytes</body></html>",
        )
        self.extractor = StubExtractor()
        self.service = RetrievedDocumentProcessingService(
            self.session,
            artifact_store=self.store,
            extractor=self.extractor,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _candidate(
            self,
            *,
            location: str,
            article_id: int | None,
    ) -> AcquisitionCandidate:
        candidate = AcquisitionCandidate(
            endpoint_id=self.endpoint.id,
            article_id=article_id,
            fingerprint=sha256(location.encode("utf-8")).hexdigest(),
            connector_id="rss",
            connector_version="1.0.0",
            external_identifier="article-1",
            location=location,
            title="Example article",
            source_identifier="example-news",
            media_type="text/html",
            language="en",
            published_at=self.now,
            first_discovered_at=self.now,
            last_discovered_at=self.now,
            discovery_count=1,
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def _attempt(
            self,
            *,
            candidate: AcquisitionCandidate,
            content: bytes,
    ) -> RetrievalAttempt:
        stored = self.store.store(content)
        raw_artifact = RawArtifactRepository(
            self.session
        ).get_or_create(stored)
        attempt = RetrievalAttempt(
            endpoint_id=self.endpoint.id,
            candidate_id=candidate.id,
            raw_artifact_id=raw_artifact.id,
            connector_id=candidate.connector_id,
            connector_version=candidate.connector_version,
            requested_location=candidate.location,
            external_identifier=candidate.external_identifier,
            discovered_at=candidate.last_discovered_at,
            retrieved_at=self.now,
            outcome=RetrievalOutcome.SUCCEEDED,
            resolved_location=candidate.location,
            response_status="200",
            content_type="text/html; charset=utf-8",
            content_hash=stored.content_hash,
            hash_algorithm=stored.hash_algorithm,
            warnings=[],
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def test_process_runs_ingestion_extraction_and_projection(self) -> None:
        result = self.service.process(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertEqual(
            self.attempt.document_version_id,
            result.ingestion.version.id,
        )
        self.assertEqual(
            result.extracted_text.payload["text"],
            "Normalized stored document text",
        )
        self.assertIs(result.projected_article, self.article)
        self.assertEqual(
            self.article.content_derived_artifact_id,
            result.extracted_text.id,
        )
        self.assertEqual(
            self.article.content,
            "Normalized stored document text",
        )
        self.assertEqual(
            self.extractor.calls,
            [(
                b"<html><body>stored source bytes</body></html>",
                "text/html; charset=utf-8",
            )],
        )

    def test_process_is_idempotent_for_same_retrieval(self) -> None:
        first = self.service.process(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )
        second = self.service.process(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertIs(first.ingestion.version, second.ingestion.version)
        self.assertIs(first.extracted_text, second.extracted_text)
        self.assertIs(first.projected_article, second.projected_article)
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

    def test_process_non_article_skips_legacy_projection(self) -> None:
        location = "https://example.com/report"
        candidate = self._candidate(
            location=location,
            article_id=None,
        )
        candidate.external_identifier = "report-1"
        self.session.flush()
        attempt = self._attempt(
            candidate=candidate,
            content=b"<html><body>report bytes</body></html>",
        )

        result = self.service.process(
            attempt=attempt,
            candidate=candidate,
            document_type=DocumentType.REPORT,
        )

        self.assertIsNone(result.projected_article)
        self.assertEqual(
            result.ingestion.document.document_type,
            DocumentType.REPORT,
        )

    def test_process_does_not_commit(self) -> None:
        article_id = self.article.id
        self.session.commit()

        self.service.process(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )
        self.session.rollback()

        article = self.session.get(Article, article_id)
        self.assertIsNone(article.content)
        self.assertIsNone(article.content_derived_artifact_id)
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

    def test_process_refuses_to_replace_legacy_content(self) -> None:
        self.article.content = "Legacy network parser text"
        self.session.flush()

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "without derived-artifact provenance",
        ):
            self.service.process(
                attempt=self.attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )

    def test_process_rejects_failed_retrieval_before_extraction(self) -> None:
        self.attempt.outcome = RetrievalOutcome.FAILED
        self.session.flush()

        with self.assertRaisesRegex(
                ValueError,
                "Only successful retrieval",
        ):
            self.service.process(
                attempt=self.attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )

        self.assertEqual(self.extractor.calls, [])


if __name__ == "__main__":
    unittest.main()
