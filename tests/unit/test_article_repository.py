import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from argus.database import Base
from argus.models import Article
from argus.storage.article_repository import ArticleRepository


class ArticleRepositoryParsingOrderTests(unittest.TestCase):
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

    def test_default_order_keeps_oldest_pending_first(self) -> None:
        with self.session_factory() as session:
            older = self._add_article(
                session,
                "Older",
                published_at=self.reference_time - timedelta(days=1),
                fetched_at=self.reference_time - timedelta(hours=2),
            )
            newer = self._add_article(
                session,
                "Newer",
                published_at=self.reference_time,
                fetched_at=self.reference_time,
            )
            repository = ArticleRepository(session)

            selected = repository.get_pending_parsing(limit=2)

        self.assertEqual(
            [article.id for article in selected],
            [older.id, newer.id],
        )

    def test_newest_order_matches_reader_feed_policy(self) -> None:
        with self.session_factory() as session:
            older = self._add_article(
                session,
                "Older",
                published_at=self.reference_time - timedelta(hours=2),
                fetched_at=self.reference_time - timedelta(hours=2),
            )
            unknown = self._add_article(
                session,
                "Unknown publication time",
                published_at=None,
                fetched_at=self.reference_time + timedelta(hours=1),
            )
            newer = self._add_article(
                session,
                "Newer",
                published_at=self.reference_time,
                fetched_at=self.reference_time,
            )
            repository = ArticleRepository(session)

            selected = repository.get_pending_parsing(
                limit=3,
                newest_first=True,
            )

        self.assertEqual(
            [article.id for article in selected],
            [newer.id, older.id, unknown.id],
        )

    def test_cursor_order_uses_oldest_unseen_ingestion_first(self) -> None:
        with self.session_factory() as session:
            delivered = self._add_article(
                session,
                "Delivered",
                published_at=self.reference_time,
                fetched_at=self.reference_time,
            )
            first_unseen = self._add_article(
                session,
                "First unseen",
                published_at=self.reference_time + timedelta(hours=2),
                fetched_at=self.reference_time + timedelta(hours=2),
            )
            second_unseen = self._add_article(
                session,
                "Second unseen",
                published_at=self.reference_time + timedelta(hours=1),
                fetched_at=self.reference_time + timedelta(hours=1),
            )
            repository = ArticleRepository(session)

            selected = repository.get_pending_parsing(
                limit=10,
                newest_first=True,
                after_article_id=delivered.id,
            )

        self.assertEqual(
            [article.id for article in selected],
            [first_unseen.id, second_unseen.id],
        )

    def _add_article(
            self,
            session,
            title: str,
            *,
            published_at: datetime | None,
            fetched_at: datetime,
    ) -> Article:
        article = Article(
            url=f"https://example.test/{title.lower().replace(' ', '-')}",
            title=title,
            published_at=published_at,
            fetched_at=fetched_at,
        )
        session.add(article)
        session.flush()
        return article


if __name__ == "__main__":
    unittest.main()
