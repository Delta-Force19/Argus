import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

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
    Article,
    CollectionEndpoint,
    DerivedArtifact,
    DocumentVersion,
    RetrievalAttempt,
    Source,
)
from argus.services.acquisition_batch_runner import (
    AcquisitionBatchItem,
    AcquisitionBatchItemStatus,
    AcquisitionBatchRunner,
)
from argus.services.acquisition_diagnostics import AcquisitionStage
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

    def discover(self, request):
        raise NotImplementedError

    def retrieve(self, candidate: CandidateRecord) -> RetrievalResult:
        if self.raises:
            raise RuntimeError("connector exploded")

        succeeded = self.outcome is RetrievalOutcome.SUCCEEDED
        return RetrievalResult(
            candidate=candidate,
            outcome=self.outcome,
            retrieved_at=datetime(
                2026,
                7,
                26,
                21,
                0,
                tzinfo=timezone.utc,
            ),
            resolved_location=candidate.location if succeeded else None,
            response_status="200" if succeeded else "404",
            content_type=(
                "text/html; charset=utf-8"
                if succeeded
                else None
            ),
            content=(
                b"<html><body>batch article</body></html>"
                if succeeded
                else None
            ),
        )


class StubExtractor:
    method = "stub-extractor"
    method_version = "1.0.0"

    def extract(
            self,
            content: bytes,
            *,
            media_type: str | None,
    ) -> ExtractedText:
        return ExtractedText(text="Normalized batch article")


class AcquisitionBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.engine = create_engine(
            f"sqlite:///{root / 'test.db'}"
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.store = FileSystemRawArtifactStore(root / "artifacts")
        self.endpoint_id, self.candidate_ids = self._seed_batch()
        self.runner = AcquisitionBatchRunner(
            self.session_factory,
            artifact_store=self.store,
            extractor=StubExtractor(),
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _seed_batch(self) -> tuple[int, tuple[int, ...]]:
        with self.session_factory() as session:
            source = Source(
                identifier="example-news",
                name="Example News",
                source_type=SourceType.NEWS_MEDIA,
                default_language="en",
            )
            session.add(source)
            session.flush()
            endpoint = CollectionEndpoint(
                identifier="example-rss",
                source_id=source.id,
                endpoint_type=EndpointType.RSS,
                connector_id="rss",
                url="https://example.com/feed.xml",
                language="en",
            )
            session.add(endpoint)
            session.flush()
            candidate_ids = []

            for number in range(3):
                location = f"https://example.com/article-{number}"
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
                record = CandidateRecord(
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
                    external_identifier=f"article-{number}",
                    title=article.title,
                    source_identifier=source.identifier,
                    language="en",
                )
                candidate = AcquisitionCandidateRepository(
                    session
                ).get_or_create(
                    endpoint=endpoint,
                    candidate=record,
                    article_id=article.id,
                )
                candidate_ids.append(candidate.id)

            endpoint_id = endpoint.id
            session.commit()
            return endpoint_id, tuple(candidate_ids)

    def _item(
            self,
            candidate_id: int,
            connector: StubConnector,
    ) -> AcquisitionBatchItem:
        return AcquisitionBatchItem(
            endpoint_id=self.endpoint_id,
            candidate_id=candidate_id,
            connector=connector,
            document_type=DocumentType.ARTICLE,
            request_metadata={"trigger": "batch"},
        )

    def test_batch_commits_success_and_retrieval_only_results(self) -> None:
        report = self.runner.run(
            (
                self._item(
                    self.candidate_ids[0],
                    StubConnector(),
                ),
                self._item(
                    self.candidate_ids[1],
                    StubConnector(
                        outcome=RetrievalOutcome.UNAVAILABLE
                    ),
                ),
            )
        )

        self.assertEqual(report.total_count, 2)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.retrieval_only_count, 1)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(
            report.items[0].status,
            AcquisitionBatchItemStatus.PROCESSED,
        )
        self.assertIsNotNone(report.items[0].document_version_id)
        self.assertEqual(
            report.items[1].retrieval_outcome,
            RetrievalOutcome.UNAVAILABLE,
        )
        self.assertEqual(
            report.items[1].url,
            "https://example.com/article-1",
        )
        self.assertEqual(
            report.items[1].failure_stage,
            AcquisitionStage.RETRIEVAL,
        )

        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(RetrievalAttempt)
                ),
                2,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(DocumentVersion)
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(DerivedArtifact)
                ),
                1,
            )

    def test_exception_rolls_back_one_item_and_continues(self) -> None:
        report = self.runner.run(
            (
                self._item(
                    self.candidate_ids[0],
                    StubConnector(raises=True),
                ),
                self._item(
                    self.candidate_ids[1],
                    StubConnector(),
                ),
            )
        )

        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(
            report.items[0].error_type,
            "RuntimeError",
        )
        self.assertEqual(
            report.items[0].error_message,
            "connector exploded",
        )
        self.assertEqual(
            report.items[0].url,
            "https://example.com/article-0",
        )
        self.assertEqual(
            report.items[0].failure_stage,
            AcquisitionStage.RETRIEVAL,
        )
        self.assertEqual(
            report.items[1].status,
            AcquisitionBatchItemStatus.PROCESSED,
        )

        with self.session_factory() as session:
            attempts = session.scalars(
                select(RetrievalAttempt)
            ).all()
            self.assertEqual(len(attempts), 1)
            self.assertEqual(
                attempts[0].candidate_id,
                self.candidate_ids[1],
            )

    def test_missing_candidate_is_reported_without_stopping_batch(
            self,
    ) -> None:
        report = self.runner.run(
            (
                self._item(999_999, StubConnector()),
                self._item(
                    self.candidate_ids[0],
                    StubConnector(),
                ),
            )
        )

        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.items[0].error_type, "LookupError")
        self.assertIn("does not exist", report.items[0].error_message)
        self.assertIsNone(report.items[0].url)
        self.assertEqual(
            report.items[0].failure_stage,
            AcquisitionStage.PREPARATION,
        )

    def test_empty_batch_returns_zero_counts(self) -> None:
        report = self.runner.run(())

        self.assertEqual(report.items, ())
        self.assertEqual(report.total_count, 0)
        self.assertEqual(report.processed_count, 0)
        self.assertEqual(report.retrieval_only_count, 0)
        self.assertEqual(report.failed_count, 0)

    def test_item_requires_persisted_positive_identifiers(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "endpoint_id must be greater than zero",
        ):
            AcquisitionBatchItem(
                endpoint_id=0,
                candidate_id=1,
                connector=StubConnector(),
                document_type=DocumentType.ARTICLE,
            )


if __name__ == "__main__":
    unittest.main()
