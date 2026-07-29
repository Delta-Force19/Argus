import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from argus.acquisition import (
    AcquisitionMode,
    CandidateRecord,
    DiscoveryRequest,
    RetrievalOutcome,
    RetrievalResult,
)


class AcquisitionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)

        self.candidate = CandidateRecord(
            connector_id="test-rss",
            connector_version="1.0.0",
            location="https://example.com/article",
            discovered_at=self.now,
            external_identifier="article-1",
            title="Example article",
            source_identifier="example-news",
            media_type="text/html",
            language="en",
            published_at=self.now,
        )

    def test_discovery_request_accepts_normalized_filters(
        self,
    ) -> None:
        request = DiscoveryRequest(
            mode=AcquisitionMode.INVESTIGATION,
            query="energy policy",
            languages=("en", "ru"),
            published_from=self.now,
            published_until=self.now,
            limit=50,
        )

        self.assertEqual(
            request.languages,
            ("en", "ru"),
        )
        self.assertEqual(request.limit, 50)

    def test_discovery_request_rejects_invalid_range(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "published_from",
        ):
            DiscoveryRequest(
                mode=AcquisitionMode.CONTINUOUS,
                published_from=datetime(
                    2026,
                    2,
                    1,
                    tzinfo=timezone.utc,
                ),
                published_until=datetime(
                    2026,
                    1,
                    1,
                    tzinfo=timezone.utc,
                ),
            )

    def test_candidate_requires_aware_discovery_time(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "discovered_at",
        ):
            CandidateRecord(
                connector_id="test-rss",
                connector_version="1.0.0",
                location="https://example.com/article",
                discovered_at=datetime(2026, 1, 1),
            )

    def test_candidate_is_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            setattr(
                self.candidate,
                "title",
                "Changed title",
            )

    def test_candidate_fingerprint_excludes_discovery_time(
        self,
    ) -> None:
        rediscovered = CandidateRecord(
            connector_id=self.candidate.connector_id,
            connector_version=self.candidate.connector_version,
            location=self.candidate.location,
            discovered_at=self.now + timedelta(hours=1),
            external_identifier=(
                self.candidate.external_identifier
            ),
            title=self.candidate.title,
            source_identifier=(
                self.candidate.source_identifier
            ),
            media_type=self.candidate.media_type,
            language=self.candidate.language,
            published_at=self.candidate.published_at,
        )

        self.assertEqual(
            rediscovered.fingerprint,
            self.candidate.fingerprint,
        )

    def test_candidate_fingerprint_changes_with_metadata(
        self,
    ) -> None:
        changed = CandidateRecord(
            connector_id=self.candidate.connector_id,
            connector_version=self.candidate.connector_version,
            location=self.candidate.location,
            discovered_at=self.now,
            external_identifier=(
                self.candidate.external_identifier
            ),
            title="Corrected headline",
            source_identifier=(
                self.candidate.source_identifier
            ),
            media_type=self.candidate.media_type,
            language=self.candidate.language,
            published_at=self.candidate.published_at,
        )

        self.assertNotEqual(
            changed.fingerprint,
            self.candidate.fingerprint,
        )

    def test_successful_retrieval_computes_content_hash(
        self,
    ) -> None:
        result = RetrievalResult(
            candidate=self.candidate,
            outcome=RetrievalOutcome.SUCCEEDED,
            retrieved_at=self.now,
            resolved_location=self.candidate.location,
            response_status="200",
            content_type="text/html",
            content=b"Argus evidence",
        )

        self.assertEqual(
            result.content_hash,
            (
                "3c1b1bdff6747487af37ee0cfe395437"
                "2aa118b98851e7a27aabb102ec4393cf"
            ),
        )

    def test_successful_retrieval_requires_content(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "must contain bytes",
        ):
            RetrievalResult(
                candidate=self.candidate,
                outcome=RetrievalOutcome.SUCCEEDED,
                retrieved_at=self.now,
            )

    def test_failed_retrieval_rejects_content(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "must not contain bytes",
        ):
            RetrievalResult(
                candidate=self.candidate,
                outcome=RetrievalOutcome.FAILED,
                retrieved_at=self.now,
                content=b"unexpected",
                error="request failed",
            )

    def test_failed_retrieval_requires_error(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "must include an error",
        ):
            RetrievalResult(
                candidate=self.candidate,
                outcome=RetrievalOutcome.FAILED,
                retrieved_at=self.now,
            )


if __name__ == "__main__":
    unittest.main()
