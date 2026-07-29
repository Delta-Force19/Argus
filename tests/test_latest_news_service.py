import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from argus.database import Base
from argus.models import Article, ProcessingState, Source
from argus.processing import (
    PARSING_METHOD_VERSION,
    ProcessingStage,
    ProcessingStatus,
)
from argus.services.latest_news_service import (
    get_highest_article_id,
    get_latest_news,
    get_news_after_article_id,
)
from argus.sources import SourceType


class LatestNewsServiceTests(unittest.TestCase):
    reference_time = datetime(
        2026,
        7,
        28,
        18,
        0,
        tzinfo=timezone.utc,
    )

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_orders_by_publication_time_and_places_unknown_time_last(
            self,
    ) -> None:
        with self.session_factory() as session:
            older = self._add_article(
                session,
                title="Older",
                published_at=self.reference_time - timedelta(hours=2),
            )
            unknown = self._add_article(
                session,
                title="Unknown time",
                published_at=None,
                fetched_at=self.reference_time + timedelta(hours=1),
            )
            newer = self._add_article(
                session,
                title="Newer",
                published_at=self.reference_time - timedelta(minutes=5),
            )
            expected_ids = [newer.id, older.id, unknown.id]
            session.commit()

        report = get_latest_news(
            session_factory=self.session_factory,
        )

        self.assertEqual(
            [item.article_id for item in report.items],
            expected_ids,
        )

    def test_uses_normalized_source_and_reports_current_parsing_state(
            self,
    ) -> None:
        with self.session_factory() as session:
            source = Source(
                identifier="example-news",
                name="Example News",
                source_type=SourceType.NEWS_MEDIA,
            )
            session.add(source)
            session.flush()
            article = self._add_article(
                session,
                source_id=source.id,
                source="Legacy Feed Name",
                content="  First line.\n\nSecond   line.  ",
            )
            session.add(
                ProcessingState(
                    article_id=article.id,
                    stage=ProcessingStage.PARSING,
                    method_version=PARSING_METHOD_VERSION,
                    status=ProcessingStatus.DONE,
                )
            )
            session.commit()

        item = get_latest_news(
            session_factory=self.session_factory,
        ).items[0]

        self.assertEqual(item.source, "Example News")
        self.assertEqual(item.parsing_status, "done")
        self.assertEqual(item.excerpt_source, "content")
        self.assertEqual(item.excerpt, "First line. Second line.")

    def test_falls_back_to_summary_legacy_source_and_safe_defaults(
            self,
    ) -> None:
        with self.session_factory() as session:
            self._add_article(
                session,
                title="  ",
                source=" Legacy Wire ",
                summary="A summary is available.",
                content=None,
                language=None,
            )
            session.commit()

        item = get_latest_news(
            session_factory=self.session_factory,
        ).items[0]

        self.assertEqual(item.title, "untitled")
        self.assertEqual(item.source, "Legacy Wire")
        self.assertEqual(item.parsing_status, "not_started")
        self.assertEqual(item.excerpt_source, "summary")
        self.assertEqual(item.excerpt, "A summary is available.")

    def test_rejects_corrupted_content_and_falls_back_to_summary(
            self,
    ) -> None:
        with self.session_factory() as session:
            self._add_article(
                session,
                content="\ufffd\ufffd6\ufffdk\ufffd" * 100,
                summary="Readable feed summary.",
            )
            session.commit()

        item = get_latest_news(
            session_factory=self.session_factory,
        ).items[0]

        self.assertEqual(item.excerpt_source, "summary")
        self.assertEqual(item.excerpt, "Readable feed summary.")

    def test_truncates_excerpt_and_honors_limit(self) -> None:
        with self.session_factory() as session:
            self._add_article(
                session,
                title="First",
                content="x" * 100,
                published_at=self.reference_time,
            )
            self._add_article(
                session,
                title="Second",
                published_at=self.reference_time - timedelta(minutes=1),
            )
            session.commit()

        report = get_latest_news(
            limit=1,
            excerpt_chars=40,
            session_factory=self.session_factory,
        )

        self.assertEqual(len(report.items), 1)
        self.assertEqual(len(report.items[0].excerpt or ""), 40)
        self.assertTrue((report.items[0].excerpt or "").endswith("…"))

    def test_is_read_only_and_validates_bounds(self) -> None:
        with self.session_factory() as session:
            self._add_article(session)
            session.commit()
            before = self._row_counts(session)

        get_latest_news(session_factory=self.session_factory)

        with self.session_factory() as session:
            after = self._row_counts(session)
        self.assertEqual(after, before)
        with self.assertRaisesRegex(ValueError, "limit"):
            get_latest_news(
                limit=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "excerpt_chars"):
            get_latest_news(
                excerpt_chars=39,
                session_factory=self.session_factory,
            )

    def test_reports_excerpt_availability_counts(self) -> None:
        with self.session_factory() as session:
            self._add_article(
                session,
                title="Full text",
                content="Extracted article body.",
            )
            self._add_article(
                session,
                title="Summary",
                summary="Feed summary.",
                published_at=self.reference_time - timedelta(minutes=1),
            )
            self._add_article(
                session,
                title="Headline only",
                published_at=self.reference_time - timedelta(minutes=2),
            )
            session.commit()

        report = get_latest_news(
            session_factory=self.session_factory,
        )

        self.assertEqual(report.content_count, 1)
        self.assertEqual(report.summary_count, 1)
        self.assertEqual(report.headline_only_count, 1)

    def test_delivery_slice_uses_ingestion_order_after_cursor(
            self,
    ) -> None:
        with self.session_factory() as session:
            first = self._add_article(
                session,
                title="Already delivered",
            )
            second = self._add_article(
                session,
                title="Late publication",
                published_at=self.reference_time - timedelta(days=2),
            )
            third = self._add_article(
                session,
                title="Newest ingestion",
                published_at=self.reference_time + timedelta(hours=1),
            )
            first_id = first.id
            expected_ids = [second.id, third.id]
            highest_id = third.id
            session.commit()

        report = get_news_after_article_id(
            after_article_id=first_id,
            limit=10,
            session_factory=self.session_factory,
        )

        self.assertEqual(
            [item.article_id for item in report.items],
            expected_ids,
        )
        self.assertEqual(
            get_highest_article_id(
                session_factory=self.session_factory,
            ),
            highest_id,
        )

    def _add_article(
            self,
            session: Session,
            *,
            title: str = "Article",
            source_id: int | None = None,
            source: str | None = None,
            published_at: datetime | None = reference_time,
            fetched_at: datetime | None = None,
            summary: str | None = None,
            content: str | None = None,
            language: str | None = "en",
    ) -> Article:
        sequence = (
            session.scalar(select(func.count()).select_from(Article))
            or 0
        ) + 1
        article = Article(
            source_id=source_id,
            url=f"https://example.test/{sequence}",
            title=title,
            source=source,
            language=language,
            published_at=published_at,
            fetched_at=fetched_at or self.reference_time,
            summary=summary,
            content=content,
        )
        session.add(article)
        session.flush()
        return article

    @staticmethod
    def _row_counts(session: Session) -> tuple[int, int]:
        return (
            session.scalar(select(func.count()).select_from(Article)) or 0,
            session.scalar(
                select(func.count()).select_from(ProcessingState)
            ) or 0,
        )


if __name__ == "__main__":
    unittest.main()
