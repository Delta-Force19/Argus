import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import CandidateRecord
from argus.database import Base
from argus.endpoints import EndpointType
from argus.models import (
    AcquisitionCandidate,
    Article,
    CollectionEndpoint,
)
from argus.storage.candidate_repository import (
    AcquisitionCandidateRepository,
    CandidateProvenanceConflict,
)


class AcquisitionCandidateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = AcquisitionCandidateRepository(
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
            20,
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
            source_identifier="example-news",
            media_type="text/html",
            language="en",
            published_at=self.timestamp,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_get_or_create_adds_candidate_without_commit(
        self,
    ) -> None:
        stored = self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=self.candidate,
        )

        self.assertIsNotNone(stored.id)
        self.assertEqual(stored.endpoint_id, self.endpoint.id)
        self.assertEqual(
            stored.fingerprint,
            self.candidate.fingerprint,
        )
        self.assertEqual(stored.discovery_count, 1)

        self.session.rollback()

        self.assertIsNone(
            self.session.scalar(
                select(AcquisitionCandidate)
            )
        )

    def test_get_or_create_records_rediscovery(
        self,
    ) -> None:
        first = self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=self.candidate,
        )
        self.session.commit()
        later_candidate = CandidateRecord(
            connector_id=self.candidate.connector_id,
            connector_version=self.candidate.connector_version,
            location=self.candidate.location,
            discovered_at=self.timestamp + timedelta(hours=1),
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

        second = self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=later_candidate,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.discovery_count, 2)
        self.assertEqual(
            second.last_discovered_at.replace(
                tzinfo=None
            ),
            later_candidate.discovered_at.replace(
                tzinfo=None
            ),
        )

    def test_changed_metadata_creates_new_snapshot(
        self,
    ) -> None:
        self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=self.candidate,
        )
        changed = CandidateRecord(
            connector_id=self.candidate.connector_id,
            connector_version=self.candidate.connector_version,
            location=self.candidate.location,
            discovered_at=self.timestamp,
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

        second = self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=changed,
        )
        candidate_count = self.session.scalar(
            select(func.count()).select_from(
                AcquisitionCandidate
            )
        )

        self.assertEqual(candidate_count, 2)
        self.assertNotEqual(
            second.fingerprint,
            self.candidate.fingerprint,
        )

    def test_get_or_create_links_legacy_article(
        self,
    ) -> None:
        article = Article(
            url=self.candidate.location,
            title="Example article",
        )
        self.session.add(article)
        self.session.flush()

        stored = self.repository.get_or_create(
            endpoint=self.endpoint,
            candidate=self.candidate,
            article_id=article.id,
        )

        self.assertEqual(stored.article_id, article.id)

    def test_get_or_create_rejects_connector_conflict(
        self,
    ) -> None:
        candidate = CandidateRecord(
            connector_id="rest-api",
            connector_version="1.0.0",
            location="https://example.com/article",
            discovered_at=self.timestamp,
        )

        with self.assertRaises(
            CandidateProvenanceConflict
        ):
            self.repository.get_or_create(
                endpoint=self.endpoint,
                candidate=candidate,
            )


if __name__ == "__main__":
    unittest.main()
