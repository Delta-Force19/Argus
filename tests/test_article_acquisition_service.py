import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from argus.acquisition import (
    CandidateRecord,
    RetrievalOutcome,
    RetrievalResult,
)
from argus.config import RSSFeedConfig
from argus.database import Base
from argus.documents import DocumentType
from argus.endpoints import EndpointType
from argus.extraction import ExtractedText
from argus.models import Article, CollectionEndpoint, Source
from argus.services.acquisition_batch_runner import AcquisitionBatchRunner
from argus.services.article_acquisition_service import (
    MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS,
    SOURCE_ACCESS_RESTRICTION_COOLDOWN,
    SOURCE_ACCESS_RESTRICTION_THRESHOLD,
    _pending_article_items,
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
            outcome: RetrievalOutcome = RetrievalOutcome.SUCCEEDED,
            *,
            retrieved_at: datetime | None = None,
    ) -> None:
        self.outcome = outcome
        self.retrieved_at = (
            retrieved_at
            if retrieved_at is not None
            else datetime(
                2026,
                7,
                27,
                18,
                0,
                tzinfo=timezone.utc,
            )
        )

    def discover(self, request):
        raise NotImplementedError

    def retrieve(self, candidate: CandidateRecord) -> RetrievalResult:
        succeeded = self.outcome is RetrievalOutcome.SUCCEEDED
        return RetrievalResult(
            candidate=candidate,
            outcome=self.outcome,
            retrieved_at=self.retrieved_at,
            resolved_location=(
                candidate.location
                if succeeded
                else None
            ),
            response_status="200" if succeeded else "404",
            content_type="text/html" if succeeded else None,
            content=(
                b"<html><body>article</body></html>"
                if succeeded
                else None
            ),
            error=(
                "The read operation timed out"
                if self.outcome is RetrievalOutcome.FAILED
                else None
            ),
        )


class StubExtractor:
    method = "stub"
    method_version = "1.0.0"

    def extract(
            self,
            content: bytes,
            *,
            media_type: str | None,
    ) -> ExtractedText:
        return ExtractedText(text="Normalized article")


class ArticleAcquisitionServiceTests(unittest.TestCase):
    selection_time = datetime(
        2026,
        7,
        27,
        19,
        0,
        tzinfo=timezone.utc,
    )

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.engine = create_engine(
            f"sqlite:///{root / 'test.db'}"
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.store = FileSystemRawArtifactStore(root / "artifacts")
        self.feed = RSSFeedConfig(
            name="Example News",
            source_identifier="example-news",
            endpoint_identifier="example-rss",
            url="https://example.com/feed.xml",
            language="en",
            country="Test",
        )
        self.feeds_by_endpoint = {
            self.feed.effective_endpoint_identifier: self.feed,
        }
        self.endpoint_id, self.candidate_ids = self._seed_candidates()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _seed_candidates(self) -> tuple[int, tuple[int, ...]]:
        with self.session_factory() as session:
            source = Source(
                identifier="example-news",
                name="Example News",
                source_type=SourceType.NEWS_MEDIA,
            )
            session.add(source)
            session.flush()
            endpoint = CollectionEndpoint(
                identifier="example-rss",
                source_id=source.id,
                endpoint_type=EndpointType.RSS,
                connector_id="rss",
                url=self.feed.url,
                language="en",
            )
            session.add(endpoint)
            session.flush()
            candidate_ids = []

            for number in range(4):
                location = f"https://example.com/{number}"
                document = DocumentRepository(session).get_or_create(
                    identifier_scheme="uri",
                    identifier_value=location,
                    document_type=DocumentType.ARTICLE,
                    source_id=source.id,
                    title=f"Article {number}",
                    language="en",
                )
                article = Article(
                    document_id=document.id,
                    source_id=source.id,
                    url=location,
                    title=f"Article {number}",
                    language="en",
                )
                session.add(article)
                session.flush()
                candidate = AcquisitionCandidateRepository(
                    session
                ).get_or_create(
                    endpoint=endpoint,
                    candidate=CandidateRecord(
                        connector_id="rss",
                        connector_version="1.0.0",
                        location=location,
                        discovered_at=datetime(
                            2026,
                            7,
                            26,
                            20,
                            number,
                            tzinfo=timezone.utc,
                        ),
                        source_identifier=source.identifier,
                        language="en",
                    ),
                    article_id=article.id,
                )
                candidate_ids.append(candidate.id)

            session.commit()
            return endpoint.id, tuple(candidate_ids)

    def _seed_other_source_candidate(self) -> int:
        feed = RSSFeedConfig(
            name="Other News",
            source_identifier="other-news",
            endpoint_identifier="other-rss",
            url="https://other.example/feed.xml",
            language="en",
            country="Test",
        )
        self.feeds_by_endpoint[
            feed.effective_endpoint_identifier
        ] = feed

        with self.session_factory() as session:
            source = Source(
                identifier=feed.source_identifier,
                name=feed.name,
                source_type=SourceType.NEWS_MEDIA,
            )
            session.add(source)
            session.flush()
            endpoint = CollectionEndpoint(
                identifier=feed.effective_endpoint_identifier,
                source_id=source.id,
                endpoint_type=EndpointType.RSS,
                connector_id="rss",
                url=feed.url,
                language="en",
            )
            session.add(endpoint)
            session.flush()
            location = "https://other.example/article"
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value=location,
                document_type=DocumentType.ARTICLE,
                source_id=source.id,
                title="Other article",
                language="en",
            )
            article = Article(
                document_id=document.id,
                source_id=source.id,
                url=location,
                title="Other article",
                language="en",
            )
            session.add(article)
            session.flush()
            candidate = AcquisitionCandidateRepository(
                session
            ).get_or_create(
                endpoint=endpoint,
                candidate=CandidateRecord(
                    connector_id="rss",
                    connector_version="1.0.0",
                    location=location,
                    discovered_at=datetime(
                        2026,
                        7,
                        27,
                        18,
                        30,
                        tzinfo=timezone.utc,
                    ),
                    source_identifier=source.identifier,
                    language="en",
                ),
                article_id=article.id,
            )
            session.commit()
            return candidate.id

    def _select(
            self,
            *,
            limit: int = 20,
            retry_unsuccessful: bool = False,
    ):
        with patch(
            "argus.services.article_acquisition_service.SessionLocal",
            self.session_factory,
        ):
            return _pending_article_items(
                limit=limit,
                retry_unsuccessful=retry_unsuccessful,
                feeds_by_endpoint=self.feeds_by_endpoint,
                selection_time=self.selection_time,
            )

    def _run_outcomes(
            self,
            outcomes: tuple[RetrievalOutcome, ...],
            *,
            retrieved_at: datetime | None = None,
    ) -> None:
        runner = AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        )

        for candidate_id, outcome in zip(
                self.candidate_ids[:len(outcomes)],
                outcomes,
                strict=True,
        ):
            item = self._select(limit=1)[0]
            self.assertEqual(item.candidate_id, candidate_id)
            runner.run(
                (
                    item.__class__(
                        endpoint_id=item.endpoint_id,
                        candidate_id=item.candidate_id,
                        connector=StubConnector(
                            outcome,
                            retrieved_at=retrieved_at,
                        ),
                        document_type=item.document_type,
                    ),
                )
            )

    def test_selects_unattempted_candidates_in_stable_order(self) -> None:
        items = self._select(limit=2)

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[:2],
        )
        self.assertTrue(
            all(item.endpoint_id == self.endpoint_id for item in items)
        )

    def test_default_selection_excludes_any_attempted_candidate(self) -> None:
        first_item = self._select(limit=1)[0]
        runner = AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        )
        runner.run(
            (
                first_item.__class__(
                    endpoint_id=first_item.endpoint_id,
                    candidate_id=first_item.candidate_id,
                    connector=StubConnector(),
                    document_type=first_item.document_type,
                ),
            )
        )

        items = self._select()

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[1:],
        )

    def test_retry_never_selects_successful_candidate(self) -> None:
        first_item = self._select(limit=1)[0]
        AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        ).run(
            (
                first_item.__class__(
                    endpoint_id=first_item.endpoint_id,
                    candidate_id=first_item.candidate_id,
                    connector=StubConnector(),
                    document_type=first_item.document_type,
                ),
            )
        )

        items = self._select(retry_unsuccessful=True)

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[1:],
        )

    def test_retry_selects_previously_unsuccessful_candidate(self) -> None:
        first_item = self._select(limit=1)[0]
        AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        ).run(
            (
                first_item.__class__(
                    endpoint_id=first_item.endpoint_id,
                    candidate_id=first_item.candidate_id,
                    connector=StubConnector(
                        RetrievalOutcome.UNAVAILABLE
                    ),
                    document_type=first_item.document_type,
                ),
            )
        )

        default_items = self._select()
        retry_items = self._select(retry_unsuccessful=True)

        self.assertEqual(
            tuple(item.candidate_id for item in default_items),
            self.candidate_ids[1:],
        )
        self.assertEqual(
            tuple(item.candidate_id for item in retry_items),
            self.candidate_ids,
        )

    def test_retry_excludes_access_restricted_candidate(self) -> None:
        first_item = self._select(limit=1)[0]
        AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        ).run(
            (
                first_item.__class__(
                    endpoint_id=first_item.endpoint_id,
                    candidate_id=first_item.candidate_id,
                    connector=StubConnector(
                        RetrievalOutcome.ACCESS_RESTRICTED
                    ),
                    document_type=first_item.document_type,
                ),
            )
        )

        items = self._select(retry_unsuccessful=True)

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[1:],
        )

    def test_retry_stops_after_maximum_attempt_count(self) -> None:
        first_item = self._select(limit=1)[0]
        runner = AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        )

        for _ in range(MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS):
            runner.run(
                (
                    first_item.__class__(
                        endpoint_id=first_item.endpoint_id,
                        candidate_id=first_item.candidate_id,
                        connector=StubConnector(
                            RetrievalOutcome.UNAVAILABLE
                        ),
                        document_type=first_item.document_type,
                    ),
                )
            )

        items = self._select(retry_unsuccessful=True)

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[1:],
        )

    def test_consecutive_access_restrictions_pause_source(self) -> None:
        other_candidate_id = self._seed_other_source_candidate()
        self._run_outcomes(
            (
                RetrievalOutcome.ACCESS_RESTRICTED,
            ) * SOURCE_ACCESS_RESTRICTION_THRESHOLD
        )

        with self.assertLogs(
            "argus.services.article_acquisition_service",
            level="INFO",
        ) as captured:
            items = self._select()

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            (other_candidate_id,),
        )
        self.assertTrue(
            any(
                "example-news" in message
                for message in captured.output
            )
        )

    def test_failed_attempt_breaks_access_restriction_streak(self) -> None:
        self._run_outcomes(
            (
                RetrievalOutcome.ACCESS_RESTRICTED,
                RetrievalOutcome.FAILED,
                RetrievalOutcome.ACCESS_RESTRICTED,
            )
        )

        items = self._select()

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[3:],
        )

    def test_source_pause_expires_after_cooldown(self) -> None:
        old_retrieval_time = (
            self.selection_time
            - SOURCE_ACCESS_RESTRICTION_COOLDOWN
            - timedelta(seconds=1)
        )
        self._run_outcomes(
            (
                RetrievalOutcome.ACCESS_RESTRICTED,
            ) * SOURCE_ACCESS_RESTRICTION_THRESHOLD,
            retrieved_at=old_retrieval_time,
        )

        items = self._select()

        self.assertEqual(
            tuple(item.candidate_id for item in items),
            self.candidate_ids[3:],
        )

    def test_unknown_endpoint_configuration_is_skipped(self) -> None:
        with self.assertLogs(
            "argus.services.article_acquisition_service",
            level="WARNING",
        ):
            items = self._select_with_no_feeds()

        self.assertEqual(items, ())

    def _select_with_no_feeds(self):
        with patch(
            "argus.services.article_acquisition_service.SessionLocal",
            self.session_factory,
        ):
            return _pending_article_items(
                limit=20,
                retry_unsuccessful=False,
                feeds_by_endpoint={},
                selection_time=self.selection_time,
            )


if __name__ == "__main__":
    unittest.main()
