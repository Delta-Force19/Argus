import unittest
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import (
    CandidateRecord,
    RetrievalOutcome,
    RetrievalResult,
)
from argus.database import Base
from argus.endpoints import EndpointType
from argus.models import (
    CollectionEndpoint,
    RawArtifact,
    RetrievalAttempt,
)
from argus.storage.retrieval_repository import (
    RetrievalAttemptRepository,
    RetrievalProvenanceConflict,
)


class RetrievalAttemptRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = RetrievalAttemptRepository(
            self.session
        )
        self.endpoint = CollectionEndpoint(
            identifier="example-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
            language="en",
        )
        self.session.add(self.endpoint)
        self.session.flush()
        self.timestamp = datetime(
            2026,
            7,
            19,
            12,
            0,
            tzinfo=timezone.utc,
        )
        self.candidate = CandidateRecord(
            connector_id="rss",
            connector_version="1.0.0",
            location="https://example.com/article",
            discovered_at=self.timestamp,
            external_identifier="article-1",
            title="Example article",
            language="en",
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_record_result_persists_success_without_commit(
        self,
    ) -> None:
        content = b"retrieved content"
        content_hash = sha256(content).hexdigest()
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.SUCCEEDED,
            retrieved_at=self.timestamp,
            resolved_location=(
                "https://example.com/resolved-article"
            ),
            response_status="200",
            content_type="text/html; charset=utf-8",
            content=content,
            warnings=("Example warning",),
        )
        raw_artifact = RawArtifact(
            hash_algorithm="sha256",
            content_hash=content_hash,
            byte_size=len(content),
            storage_backend="filesystem",
            storage_key=(
                f"sha256/{content_hash[:2]}/"
                f"{content_hash[2:]}"
            ),
        )
        self.session.add(raw_artifact)
        self.session.flush()

        attempt = self.repository.record_result(
            endpoint=self.endpoint,
            result=result,
            raw_artifact=raw_artifact,
            request_metadata={
                "timeout_seconds": 30,
                "follow_redirects": True,
            },
        )

        self.assertIsNotNone(attempt.id)
        self.assertEqual(attempt.endpoint_id, self.endpoint.id)
        self.assertEqual(
            attempt.raw_artifact_id,
            raw_artifact.id,
        )
        self.assertEqual(attempt.connector_id, "rss")
        self.assertEqual(attempt.connector_version, "1.0.0")
        self.assertEqual(
            attempt.requested_location,
            "https://example.com/article",
        )
        self.assertEqual(attempt.external_identifier, "article-1")
        self.assertEqual(
            attempt.outcome,
            RetrievalOutcome.SUCCEEDED,
        )
        self.assertEqual(attempt.content_hash, result.content_hash)
        self.assertEqual(attempt.hash_algorithm, "sha256")
        self.assertEqual(attempt.warnings, ["Example warning"])
        self.assertEqual(
            attempt.request_metadata,
            {
                "timeout_seconds": 30,
                "follow_redirects": True,
            },
        )

        self.session.rollback()

        self.assertIsNone(
            self.session.scalar(
                select(RetrievalAttempt)
            )
        )

    def test_record_result_persists_failed_attempt(
        self,
    ) -> None:
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.FAILED,
            retrieved_at=self.timestamp,
            response_status="503",
            error="Service unavailable.",
        )

        attempt = self.repository.record_result(
            endpoint=self.endpoint,
            result=result,
        )

        self.assertEqual(
            attempt.outcome,
            RetrievalOutcome.FAILED,
        )
        self.assertEqual(attempt.response_status, "503")
        self.assertEqual(attempt.error, "Service unavailable.")
        self.assertIsNone(attempt.content_hash)
        self.assertIsNone(attempt.hash_algorithm)
        self.assertEqual(attempt.warnings, [])

    def test_successful_result_requires_persisted_artifact(
        self,
    ) -> None:
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.SUCCEEDED,
            retrieved_at=self.timestamp,
            content=b"retrieved content",
        )

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "requires a persisted raw artifact",
        ):
            self.repository.record_result(
                endpoint=self.endpoint,
                result=result,
            )

    def test_successful_result_rejects_artifact_mismatch(
        self,
    ) -> None:
        content = b"retrieved content"
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.SUCCEEDED,
            retrieved_at=self.timestamp,
            content=content,
        )
        raw_artifact = RawArtifact(
            hash_algorithm="sha256",
            content_hash="00" * 32,
            byte_size=len(content),
            storage_backend="filesystem",
            storage_key=f"sha256/00/{'00' * 31}",
        )
        self.session.add(raw_artifact)
        self.session.flush()

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "does not match retrieved content",
        ):
            self.repository.record_result(
                endpoint=self.endpoint,
                result=result,
                raw_artifact=raw_artifact,
            )

    def test_unsuccessful_result_rejects_artifact(
        self,
    ) -> None:
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.UNAVAILABLE,
            retrieved_at=self.timestamp,
        )
        raw_artifact = RawArtifact(
            hash_algorithm="sha256",
            content_hash="00" * 32,
            byte_size=0,
            storage_backend="filesystem",
            storage_key=f"sha256/00/{'00' * 31}",
        )
        self.session.add(raw_artifact)
        self.session.flush()

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "must not reference a raw artifact",
        ):
            self.repository.record_result(
                endpoint=self.endpoint,
                result=result,
                raw_artifact=raw_artifact,
            )

    def test_record_result_allows_repeated_attempts(
        self,
    ) -> None:
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.UNAVAILABLE,
            retrieved_at=self.timestamp,
            response_status="404",
        )

        self.repository.record_result(
            endpoint=self.endpoint,
            result=result,
        )
        self.repository.record_result(
            endpoint=self.endpoint,
            result=result,
        )

        attempt_count = self.session.scalar(
            select(func.count()).select_from(
                RetrievalAttempt
            )
        )

        self.assertEqual(attempt_count, 2)

    def test_record_result_rejects_connector_conflict(
        self,
    ) -> None:
        candidate = CandidateRecord(
            connector_id="rest-api",
            connector_version="1.0.0",
            location="https://example.com/article",
            discovered_at=self.timestamp,
        )
        result = RetrievalResult(
            candidate=candidate,
            outcome=RetrievalOutcome.UNAVAILABLE,
            retrieved_at=self.timestamp,
        )

        with self.assertRaises(
            RetrievalProvenanceConflict
        ):
            self.repository.record_result(
                endpoint=self.endpoint,
                result=result,
            )


if __name__ == "__main__":
    unittest.main()
