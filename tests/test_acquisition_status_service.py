import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from argus.acquisition import CandidateRecord, RetrievalOutcome
from argus.database import Base
from argus.documents import DocumentType
from argus.endpoints import EndpointType
from argus.models import (
    Article,
    CollectionEndpoint,
    RetrievalAttempt,
    Source,
)
from argus.services.acquisition_status_service import (
    AcquisitionStatusReport,
    get_acquisition_status,
)
from argus.services.article_acquisition_service import (
    MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS,
    SOURCE_ACCESS_RESTRICTION_COOLDOWN,
)
from argus.sources import SourceType
from argus.storage.candidate_repository import (
    AcquisitionCandidateRepository,
)
from argus.storage.document_repository import DocumentRepository


class AcquisitionStatusServiceTests(unittest.TestCase):
    status_time = datetime(
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

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _status(self) -> AcquisitionStatusReport:
        with patch(
            "argus.services.acquisition_status_service.SessionLocal",
            self.session_factory,
        ):
            return get_acquisition_status(
                status_time=self.status_time,
            )

    def _seed_source(
            self,
            *,
            source_name: str = "Example News",
            connector_id: str = "rss",
            is_active: bool = True,
            outcomes: tuple[tuple[RetrievalOutcome, ...], ...],
    ) -> None:
        source_identifier = source_name.lower().replace(" ", "-")
        with self.session_factory() as session:
            source = Source(
                identifier=source_identifier,
                name=source_name,
                source_type=SourceType.NEWS_MEDIA,
            )
            session.add(source)
            session.flush()
            endpoint = CollectionEndpoint(
                identifier=f"{source_identifier}-endpoint",
                source_id=source.id,
                endpoint_type=EndpointType.RSS,
                connector_id=connector_id,
                url=f"https://{source_identifier}.example/feed.xml",
                language="en",
                is_active=is_active,
            )
            session.add(endpoint)
            session.flush()

            for candidate_number, candidate_outcomes in enumerate(outcomes):
                location = (
                    f"https://{source_identifier}.example/"
                    f"{candidate_number}"
                )
                document = DocumentRepository(session).get_or_create(
                    identifier_scheme="uri",
                    identifier_value=location,
                    document_type=DocumentType.ARTICLE,
                    source_id=source.id,
                    title=f"Article {candidate_number}",
                    language="en",
                )
                article = Article(
                    document_id=document.id,
                    source_id=source.id,
                    url=location,
                    title=f"Article {candidate_number}",
                    language="en",
                )
                session.add(article)
                session.flush()
                candidate_record = CandidateRecord(
                    connector_id=connector_id,
                    connector_version="1.0.0",
                    location=location,
                    discovered_at=self.status_time - timedelta(hours=2),
                    source_identifier=source.identifier,
                    language="en",
                )
                candidate = AcquisitionCandidateRepository(
                    session
                ).get_or_create(
                    endpoint=endpoint,
                    candidate=candidate_record,
                    article_id=article.id,
                )

                for attempt_number, outcome in enumerate(
                        candidate_outcomes
                ):
                    session.add(
                        RetrievalAttempt(
                            endpoint_id=endpoint.id,
                            article_id=article.id,
                            candidate_id=candidate.id,
                            connector_id=connector_id,
                            connector_version="1.0.0",
                            requested_location=location,
                            discovered_at=candidate_record.discovered_at,
                            retrieved_at=(
                                self.status_time
                                - timedelta(minutes=attempt_number)
                            ),
                            outcome=outcome,
                            warnings=[],
                        )
                    )

            session.commit()

    def test_empty_queue_returns_zero_counts(self) -> None:
        self.assertEqual(
            self._status(),
            AcquisitionStatusReport(
                total=0,
                unattempted=0,
                succeeded=0,
                retryable=0,
                access_restricted=0,
                exhausted=0,
                paused_sources=(),
            ),
        )

    def test_reports_mutually_exclusive_candidate_states(self) -> None:
        self._seed_source(
            outcomes=(
                (),
                (RetrievalOutcome.SUCCEEDED,),
                (RetrievalOutcome.UNAVAILABLE,),
                (RetrievalOutcome.ACCESS_RESTRICTED,),
                (
                    (RetrievalOutcome.FAILED,)
                    * MAX_AUTOMATIC_RETRIEVAL_ATTEMPTS
                ),
            ),
        )

        report = self._status()

        self.assertEqual(report.total, 5)
        self.assertEqual(report.unattempted, 1)
        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.retryable, 1)
        self.assertEqual(report.access_restricted, 1)
        self.assertEqual(report.exhausted, 1)
        self.assertEqual(
            report.total,
            (
                report.unattempted
                + report.succeeded
                + report.retryable
                + report.access_restricted
                + report.exhausted
            ),
        )

    def test_reports_paused_sources(self) -> None:
        self._seed_source(
            source_name="The Telegraph",
            outcomes=(
                (RetrievalOutcome.ACCESS_RESTRICTED,),
                (RetrievalOutcome.ACCESS_RESTRICTED,),
                (RetrievalOutcome.ACCESS_RESTRICTED,),
            ),
        )

        report = self._status()

        self.assertEqual(
            report.paused_sources,
            ("the-telegraph",),
        )

    def test_excludes_inactive_and_non_rss_candidates(self) -> None:
        self._seed_source(
            source_name="Inactive News",
            is_active=False,
            outcomes=((),),
        )
        self._seed_source(
            source_name="API News",
            connector_id="api",
            outcomes=((),),
        )

        self.assertEqual(self._status().total, 0)


if __name__ == "__main__":
    unittest.main()
