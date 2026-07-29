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
from argus.endpoints import EndpointType
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
    RawArtifact,
    RetrievalAttempt,
)
from argus.services.retrieval_service import RetrievalService
from argus.storage.artifact_store import (
    FileSystemRawArtifactStore,
)
from argus.storage.candidate_repository import (
    AcquisitionCandidateRepository,
)
from argus.storage.retrieval_repository import (
    RetrievalProvenanceConflict,
)


class StubConnector:
    def __init__(
        self,
        *,
        outcome: RetrievalOutcome = RetrievalOutcome.SUCCEEDED,
        content: bytes | None = b"retrieved content",
        connector_id: str = "rss",
        connector_version: str = "1.0.0",
        returned_candidate: CandidateRecord | None = None,
    ) -> None:
        self.connector_id = connector_id
        self.connector_version = connector_version
        self.outcome = outcome
        self.content = content
        self.returned_candidate = returned_candidate
        self.retrieved_candidates: list[CandidateRecord] = []

    def discover(self, request):
        raise NotImplementedError

    def retrieve(
        self,
        candidate: CandidateRecord,
    ) -> RetrievalResult:
        self.retrieved_candidates.append(candidate)
        result_candidate = self.returned_candidate or candidate

        return RetrievalResult(
            candidate=result_candidate,
            outcome=self.outcome,
            retrieved_at=datetime(
                2026,
                7,
                21,
                14,
                0,
                tzinfo=timezone.utc,
            ),
            resolved_location=(
                result_candidate.location
                if self.outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            response_status=(
                "200"
                if self.outcome is RetrievalOutcome.SUCCEEDED
                else "503"
            ),
            content_type=(
                "text/html"
                if self.outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            content=(
                self.content
                if self.outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            error=(
                "Service unavailable."
                if self.outcome is RetrievalOutcome.FAILED
                else None
            ),
        )


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.artifact_root = Path(
            self.temporary_directory.name
        ) / "artifacts"
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.endpoint = CollectionEndpoint(
            identifier="example-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
            language="en",
        )
        self.session.add(self.endpoint)
        self.session.flush()
        self.discovered_at = datetime(
            2026,
            7,
            21,
            12,
            0,
            tzinfo=timezone.utc,
        )
        self.candidate_record = CandidateRecord(
            connector_id="rss",
            connector_version="1.0.0",
            location="https://example.com/article",
            discovered_at=self.discovered_at,
            external_identifier="article-1",
            title="Example article",
            source_identifier="example-news",
            media_type="text/html",
            language="en",
            published_at=self.discovered_at,
        )
        self.candidate = AcquisitionCandidateRepository(
            self.session
        ).get_or_create(
            endpoint=self.endpoint,
            candidate=self.candidate_record,
        )
        self.store = FileSystemRawArtifactStore(
            self.artifact_root
        )
        self.service = RetrievalService(
            session=self.session,
            artifact_store=self.store,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_success_persists_bytes_metadata_and_attempt(
        self,
    ) -> None:
        connector = StubConnector()

        attempt = self.service.retrieve_candidate(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
            request_metadata={"timeout_seconds": 30},
        )
        artifact = self.session.get(
            RawArtifact,
            attempt.raw_artifact_id,
        )

        self.assertIsNotNone(attempt.id)
        self.assertEqual(
            attempt.candidate_id,
            self.candidate.id,
        )
        self.assertEqual(
            attempt.outcome,
            RetrievalOutcome.SUCCEEDED,
        )
        self.assertEqual(
            attempt.request_metadata,
            {"timeout_seconds": 30},
        )
        self.assertIsNotNone(artifact)
        self.assertEqual(
            self.store.read(artifact.storage_key),
            b"retrieved content",
        )
        self.assertEqual(len(connector.retrieved_candidates), 1)
        self.assertEqual(
            connector.retrieved_candidates[0].fingerprint,
            self.candidate.fingerprint,
        )

    def test_failed_retrieval_records_attempt_without_artifact(
        self,
    ) -> None:
        connector = StubConnector(
            outcome=RetrievalOutcome.FAILED,
            content=None,
        )

        attempt = self.service.retrieve_candidate(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
        )
        artifact_count = self.session.scalar(
            select(func.count()).select_from(RawArtifact)
        )

        self.assertEqual(
            attempt.outcome,
            RetrievalOutcome.FAILED,
        )
        self.assertEqual(attempt.error, "Service unavailable.")
        self.assertIsNone(attempt.raw_artifact_id)
        self.assertEqual(artifact_count, 0)

    def test_repeated_content_reuses_artifact_and_keeps_attempts(
        self,
    ) -> None:
        connector = StubConnector()

        first_attempt = self.service.retrieve_candidate(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
        )
        second_attempt = self.service.retrieve_candidate(
            endpoint=self.endpoint,
            candidate=self.candidate,
            connector=connector,
        )
        artifact_count = self.session.scalar(
            select(func.count()).select_from(RawArtifact)
        )
        attempt_count = self.session.scalar(
            select(func.count()).select_from(RetrievalAttempt)
        )

        self.assertEqual(
            first_attempt.raw_artifact_id,
            second_attempt.raw_artifact_id,
        )
        self.assertEqual(artifact_count, 1)
        self.assertEqual(attempt_count, 2)

    def test_reconstructs_timezone_aware_contract_after_reload(
        self,
    ) -> None:
        candidate_id = self.candidate.id
        endpoint_id = self.endpoint.id
        self.session.commit()
        self.session.expire_all()
        candidate = self.session.get(
            AcquisitionCandidate,
            candidate_id,
        )
        endpoint = self.session.get(
            CollectionEndpoint,
            endpoint_id,
        )
        connector = StubConnector()

        self.service.retrieve_candidate(
            endpoint=endpoint,
            candidate=candidate,
            connector=connector,
        )

        reconstructed = connector.retrieved_candidates[0]
        self.assertIsNotNone(reconstructed.discovered_at.tzinfo)
        self.assertIsNotNone(reconstructed.published_at.tzinfo)
        self.assertEqual(
            reconstructed.fingerprint,
            candidate.fingerprint,
        )

    def test_rejects_connector_version_before_network_call(
        self,
    ) -> None:
        connector = StubConnector(
            connector_version="2.0.0"
        )

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "connector version",
        ):
            self.service.retrieve_candidate(
                endpoint=self.endpoint,
                candidate=self.candidate,
                connector=connector,
            )

        self.assertEqual(connector.retrieved_candidates, [])

    def test_rejects_tampered_candidate_before_network_call(
        self,
    ) -> None:
        self.candidate.title = "Tampered title"
        connector = StubConnector()

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "does not match its fingerprint",
        ):
            self.service.retrieve_candidate(
                endpoint=self.endpoint,
                candidate=self.candidate,
                connector=connector,
            )

        self.assertEqual(connector.retrieved_candidates, [])

    def test_rejects_mismatched_result_before_storing_bytes(
        self,
    ) -> None:
        other_candidate = CandidateRecord(
            connector_id="rss",
            connector_version="1.0.0",
            location="https://example.com/other",
            discovered_at=self.discovered_at,
        )
        connector = StubConnector(
            returned_candidate=other_candidate
        )

        with self.assertRaisesRegex(
            RetrievalProvenanceConflict,
            "Connector result does not match",
        ):
            self.service.retrieve_candidate(
                endpoint=self.endpoint,
                candidate=self.candidate,
                connector=connector,
            )

        artifact_count = self.session.scalar(
            select(func.count()).select_from(RawArtifact)
        )
        attempt_count = self.session.scalar(
            select(func.count()).select_from(RetrievalAttempt)
        )
        self.assertEqual(artifact_count, 0)
        self.assertEqual(attempt_count, 0)
        self.assertFalse(self.artifact_root.exists())


if __name__ == "__main__":
    unittest.main()
