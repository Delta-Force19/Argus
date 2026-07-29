import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.database import Base
from argus.models import Source
from argus.sources import SourceType
from argus.storage.source_repository import SourceRepository


class SourceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:"
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = SourceRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_get_or_create_adds_source_without_commit(self) -> None:
        source = self.repository.get_or_create(
            identifier="example-news",
            name="Example News",
            source_type=SourceType.NEWS_MEDIA,
            primary_jurisdiction="Example Country",
            default_language="en",
        )

        self.assertIsNotNone(source.id)
        self.assertEqual(source.identifier, "example-news")
        self.assertEqual(source.name, "Example News")
        self.assertEqual(
            source.source_type,
            SourceType.NEWS_MEDIA,
        )
        self.assertEqual(
            source.primary_jurisdiction,
            "Example Country",
        )
        self.assertEqual(source.default_language, "en")

        self.session.rollback()

        self.assertIsNone(
            self.repository.get_by_identifier(
                "example-news"
            )
        )

    def test_get_or_create_returns_existing_source(self) -> None:
        first = self.repository.get_or_create(
            identifier="example-news",
            name="Example News",
            source_type=SourceType.NEWS_MEDIA,
        )
        second = self.repository.get_or_create(
            identifier="example-news",
            name="Changed Display Name",
            source_type=SourceType.NEWS_AGENCY,
        )

        source_count = self.session.scalar(
            select(func.count()).select_from(Source)
        )

        self.assertIs(first, second)
        self.assertEqual(source_count, 1)
        self.assertEqual(second.name, "Example News")
        self.assertEqual(
            second.source_type,
            SourceType.NEWS_MEDIA,
        )

    def test_get_by_identifier_returns_none_when_missing(
            self,
    ) -> None:
        source = self.repository.get_by_identifier(
            "missing-source"
        )

        self.assertIsNone(source)

    def test_get_or_create_enriches_missing_metadata(
            self,
    ) -> None:
        source = self.repository.get_or_create(
            identifier="example-news",
            name="Example News",
            source_type=SourceType.NEWS_MEDIA,
        )

        enriched_source = self.repository.get_or_create(
            identifier="example-news",
            name="Changed Name",
            source_type=SourceType.NEWS_AGENCY,
            primary_jurisdiction="Example Country",
            default_language="en",
        )

        self.assertIs(source, enriched_source)
        self.assertEqual(
            enriched_source.primary_jurisdiction,
            "Example Country",
        )
        self.assertEqual(
            enriched_source.default_language,
            "en",
        )
        self.assertEqual(
            enriched_source.name,
            "Example News",
        )
        self.assertEqual(
            enriched_source.source_type,
            SourceType.NEWS_MEDIA,
        )


if __name__ == "__main__":
    unittest.main()