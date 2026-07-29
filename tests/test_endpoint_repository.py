import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.database import Base
from argus.endpoints import (
    EndpointConfigurationConflict,
    EndpointType,
)
from argus.models import CollectionEndpoint, Source
from argus.sources import SourceType
from argus.storage.endpoint_repository import (
    CollectionEndpointRepository,
)


class CollectionEndpointRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:"
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = CollectionEndpointRepository(
            self.session
        )

        self.source = Source(
            identifier="example-news",
            name="Example News",
            source_type=SourceType.NEWS_MEDIA,
        )
        self.session.add(self.source)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_get_or_create_adds_endpoint_without_commit(
        self,
    ) -> None:
        endpoint = self.repository.get_or_create(
            identifier="example-news-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
            source_id=self.source.id,
            language="en",
        )

        self.assertIsNotNone(endpoint.id)
        self.assertEqual(
            endpoint.source_id,
            self.source.id,
        )
        self.assertTrue(endpoint.is_active)

        self.session.rollback()

        self.assertIsNone(
            self.repository.get_by_identifier(
                "example-news-rss"
            )
        )

    def test_get_or_create_returns_existing_endpoint(
        self,
    ) -> None:
        first = self.repository.get_or_create(
            identifier="example-news-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
        )
        second = self.repository.get_or_create(
            identifier="example-news-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
            source_id=self.source.id,
            language="en",
        )

        endpoint_count = self.session.scalar(
            select(func.count()).select_from(
                CollectionEndpoint
            )
        )

        self.assertIs(first, second)
        self.assertEqual(endpoint_count, 1)
        self.assertEqual(
            second.source_id,
            self.source.id,
        )
        self.assertEqual(second.language, "en")

    def test_get_or_create_rejects_identity_conflict(
        self,
    ) -> None:
        self.repository.get_or_create(
            identifier="example-news-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
        )

        with self.assertRaisesRegex(
            EndpointConfigurationConflict,
            "url",
        ):
            self.repository.get_or_create(
                identifier="example-news-rss",
                endpoint_type=EndpointType.RSS,
                connector_id="rss",
                url="https://example.com/changed.xml",
            )

    def test_get_or_create_rejects_duplicate_location(
        self,
    ) -> None:
        self.repository.get_or_create(
            identifier="example-news-rss",
            endpoint_type=EndpointType.RSS,
            connector_id="rss",
            url="https://example.com/feed.xml",
        )

        with self.assertRaisesRegex(
            EndpointConfigurationConflict,
            "already registered",
        ):
            self.repository.get_or_create(
                identifier="duplicate-identifier",
                endpoint_type=EndpointType.RSS,
                connector_id="rss",
                url="https://example.com/feed.xml",
            )


if __name__ == "__main__":
    unittest.main()
