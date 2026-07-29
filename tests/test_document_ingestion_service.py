import unittest
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import RetrievalOutcome, StoredArtifact
from argus.database import Base
from argus.documents import (
    DocumentIngestionConflict,
    DocumentType,
)
from argus.endpoints import EndpointType
from argus.models import (
    AcquisitionCandidate,
    Article,
    CollectionEndpoint,
    Document,
    DocumentVersion,
    RetrievalAttempt,
    Source,
)
from argus.services.document_ingestion_service import (
    DocumentIngestionService,
)
from argus.sources import SourceType
from argus.storage.raw_artifact_repository import (
    RawArtifactRepository,
)


class DocumentIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.published_at = datetime(
            2026,
            7,
            21,
            12,
            0,
            tzinfo=timezone.utc,
        )
        self.candidate = self._candidate(
            external_identifier="article-1"
        )
        self.attempt = self._attempt(
            candidate=self.candidate,
            digest_byte="a",
        )
        self.service = DocumentIngestionService(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _candidate(
            self,
            *,
            external_identifier: str | None,
            location: str = "https://example.com/article",
    ) -> AcquisitionCandidate:
        candidate = AcquisitionCandidate(
            endpoint_id=self.endpoint.id,
            fingerprint=sha256(
                (external_identifier or location).encode("utf-8")
            ).hexdigest(),
            connector_id="rss",
            connector_version="1.0.0",
            external_identifier=external_identifier,
            location=location,
            title="Example article",
            source_identifier="example-news",
            media_type="text/html",
            language="en",
            published_at=self.published_at,
            first_discovered_at=self.published_at,
            last_discovered_at=self.published_at,
            discovery_count=1,
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def _attempt(
            self,
            *,
            candidate: AcquisitionCandidate,
            digest_byte: str,
            outcome: RetrievalOutcome = RetrievalOutcome.SUCCEEDED,
    ) -> RetrievalAttempt:
        raw_artifact_id = None
        content_hash = None
        hash_algorithm = None

        if outcome is RetrievalOutcome.SUCCEEDED:
            content_hash = digest_byte * 64
            artifact = RawArtifactRepository(
                self.session
            ).get_or_create(
                StoredArtifact(
                    storage_backend="filesystem",
                    storage_key=(
                        f"sha256/{content_hash[:2]}/{content_hash}"
                    ),
                    hash_algorithm="sha256",
                    content_hash=content_hash,
                    byte_size=128,
                )
            )
            raw_artifact_id = artifact.id
            hash_algorithm = "sha256"

        attempt = RetrievalAttempt(
            endpoint_id=candidate.endpoint_id,
            candidate_id=candidate.id,
            raw_artifact_id=raw_artifact_id,
            connector_id=candidate.connector_id,
            connector_version=candidate.connector_version,
            requested_location=candidate.location,
            external_identifier=candidate.external_identifier,
            discovered_at=candidate.last_discovered_at,
            retrieved_at=self.published_at,
            outcome=outcome,
            resolved_location=(
                candidate.location
                if outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            response_status=(
                "200"
                if outcome is RetrievalOutcome.SUCCEEDED
                else "503"
            ),
            content_type=(
                "text/html; charset=utf-8"
                if outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            content_hash=content_hash,
            hash_algorithm=hash_algorithm,
            error=(
                None
                if outcome is RetrievalOutcome.SUCCEEDED
                else "Service unavailable."
            ),
            warnings=[],
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def test_ingest_creates_document_version_and_attempt_link(
            self,
    ) -> None:
        result = self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertEqual(
            result.document.identifier_scheme,
            "rss:external",
        )
        self.assertEqual(
            result.document.identifier_value,
            "article-1",
        )
        self.assertEqual(result.document.source_id, self.source.id)
        self.assertEqual(result.document.title, "Example article")
        self.assertEqual(result.version.version_number, 1)
        self.assertEqual(
            result.version.media_type,
            "text/html; charset=utf-8",
        )
        self.assertEqual(
            self.attempt.document_version_id,
            result.version.id,
        )

    def test_ingest_does_not_commit(self) -> None:
        self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Document)
            ),
            0,
        )

    def test_ingest_falls_back_to_location_identity(self) -> None:
        candidate = self._candidate(
            external_identifier=None,
            location="https://example.com/location-only",
        )
        attempt = self._attempt(
            candidate=candidate,
            digest_byte="b",
        )

        result = self.service.ingest_retrieval(
            attempt=attempt,
            candidate=candidate,
            document_type=DocumentType.REPORT,
        )

        self.assertEqual(result.document.identifier_scheme, "uri")
        self.assertEqual(
            result.document.identifier_value,
            "https://example.com/location-only",
        )

    def test_repeated_ingestion_is_idempotent(self) -> None:
        first = self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )
        second = self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertIs(first.document, second.document)
        self.assertIs(first.version, second.version)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DocumentVersion)
            ),
            1,
        )

    def test_ingest_reuses_legacy_article_document(self) -> None:
        legacy_document = Document(
            source_id=self.source.id,
            document_type=DocumentType.ARTICLE,
            identifier_scheme="uri",
            identifier_value=self.candidate.location,
            title="Example article",
            language="en",
        )
        self.session.add(legacy_document)
        self.session.flush()
        article = Article(
            document_id=legacy_document.id,
            source_id=self.source.id,
            url=self.candidate.location,
            title="Example article",
            language="en",
        )
        self.session.add(article)
        self.session.flush()
        self.candidate.article_id = article.id
        self.session.flush()

        result = self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertEqual(result.document.id, legacy_document.id)
        self.assertEqual(result.document.identifier_scheme, "uri")
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Document)
            ),
            1,
        )

    def test_rejects_legacy_article_location_mismatch(self) -> None:
        article = Article(
            source_id=self.source.id,
            url="https://example.com/another-article",
            title="Another article",
        )
        self.session.add(article)
        self.session.flush()
        self.candidate.article_id = article.id
        self.session.flush()

        with self.assertRaisesRegex(
            DocumentIngestionConflict,
            "location",
        ):
            self.service.ingest_retrieval(
                attempt=self.attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )

    def test_rejects_invalid_legacy_document_link(self) -> None:
        other_document = Document(
            source_id=self.source.id,
            document_type=DocumentType.ARTICLE,
            identifier_scheme="uri",
            identifier_value="https://example.com/other-document",
            title="Other document",
        )
        self.session.add(other_document)
        self.session.flush()
        article = Article(
            document_id=other_document.id,
            source_id=self.source.id,
            url=self.candidate.location,
            title="Example article",
        )
        self.session.add(article)
        self.session.flush()
        self.candidate.article_id = article.id
        self.session.flush()

        with self.assertRaisesRegex(
            DocumentIngestionConflict,
            "document identity",
        ):
            self.service.ingest_retrieval(
                attempt=self.attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )

    def test_changed_bytes_create_next_version(self) -> None:
        first = self.service.ingest_retrieval(
            attempt=self.attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )
        second_attempt = self._attempt(
            candidate=self.candidate,
            digest_byte="c",
        )
        second = self.service.ingest_retrieval(
            attempt=second_attempt,
            candidate=self.candidate,
            document_type=DocumentType.ARTICLE,
        )

        self.assertEqual(first.version.version_number, 1)
        self.assertEqual(second.version.version_number, 2)
        self.assertNotEqual(first.version.id, second.version.id)

    def test_rejects_unsuccessful_retrieval(self) -> None:
        attempt = self._attempt(
            candidate=self.candidate,
            digest_byte="d",
            outcome=RetrievalOutcome.FAILED,
        )

        with self.assertRaisesRegex(
            DocumentIngestionConflict,
            "successful retrieval",
        ):
            self.service.ingest_retrieval(
                attempt=attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )

    def test_rejects_another_candidate(self) -> None:
        other_candidate = self._candidate(
            external_identifier="article-2",
            location="https://example.com/other",
        )

        with self.assertRaisesRegex(
            DocumentIngestionConflict,
            "supplied candidate",
        ):
            self.service.ingest_retrieval(
                attempt=self.attempt,
                candidate=other_candidate,
                document_type=DocumentType.ARTICLE,
            )

    def test_rejects_mismatched_artifact_metadata(self) -> None:
        self.attempt.content_hash = "f" * 64

        with self.assertRaisesRegex(
            DocumentIngestionConflict,
            "raw artifact",
        ):
            self.service.ingest_retrieval(
                attempt=self.attempt,
                candidate=self.candidate,
                document_type=DocumentType.ARTICLE,
            )


if __name__ == "__main__":
    unittest.main()
