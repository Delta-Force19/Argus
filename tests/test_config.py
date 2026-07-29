import unittest
from dataclasses import FrozenInstanceError

from argus.sources import SourceType

from argus.config import (
    DATABASE_PATH,
    LOG_FILE,
    PROJECT_ROOT,
    RAW_ARTIFACT_DIRECTORY,
    RSS_FEEDS,
    RSSFeedConfig,
)


class ConfigTests(unittest.TestCase):
    def test_project_paths_are_derived_from_project_root(
        self,
    ) -> None:
        self.assertEqual(
            DATABASE_PATH,
            PROJECT_ROOT / "data" / "database" / "argus.db",
            )
        self.assertEqual(
            LOG_FILE,
            PROJECT_ROOT / "logs" / "argus.log",
            )
        self.assertEqual(
            RAW_ARTIFACT_DIRECTORY,
            PROJECT_ROOT / "data" / "raw_artifacts",
            )

    def test_rss_feeds_are_stored_as_tuple(self) -> None:
        self.assertIsInstance(RSS_FEEDS, tuple)
        self.assertGreater(len(RSS_FEEDS), 0)

    def test_rss_feed_has_required_metadata(self) -> None:
        feed = RSS_FEEDS[0]

        self.assertIsInstance(feed, RSSFeedConfig)
        self.assertTrue(feed.name)
        self.assertTrue(feed.url)
        self.assertTrue(feed.language)
        self.assertTrue(feed.country)

        self.assertTrue(
            feed.effective_source_identifier
        )
        self.assertTrue(
            feed.effective_endpoint_identifier
        )
        self.assertIsInstance(
            feed.source_type,
            SourceType,
        )

    def test_source_identifier_defaults_to_name(self) -> None:
        feed = RSSFeedConfig(
            name="Example News",
            url="https://example.com/rss",
            language="en",
            country="Example Country",
        )

        self.assertEqual(
            feed.effective_source_identifier,
            "Example News",
        )

    def test_explicit_source_identifier_is_preserved(self) -> None:
        feed = RSSFeedConfig(
            name="Example World Service",
            url="https://example.com/world/rss",
            language="en",
            country="Example Country",
            source_identifier="example-news",
            source_type=SourceType.NEWS_AGENCY,
        )

        self.assertEqual(
            feed.effective_source_identifier,
            "example-news",
        )
        self.assertEqual(
            feed.source_type,
            SourceType.NEWS_AGENCY,
        )

    def test_endpoint_identifier_has_deterministic_fallback(
        self,
    ) -> None:
        feed = RSSFeedConfig(
            name="Example World Service",
            url="https://example.com/world/rss",
            language="en",
            country="Example Country",
            source_identifier="example-news",
        )

        self.assertEqual(
            feed.effective_endpoint_identifier,
            "rss:example-news",
        )

    def test_explicit_endpoint_identifier_is_preserved(
        self,
    ) -> None:
        feed = RSSFeedConfig(
            name="Example Business",
            url="https://example.com/business/rss",
            language="en",
            country="Example Country",
            source_identifier="example-news",
            endpoint_identifier="example-business-en",
        )

        self.assertEqual(
            feed.effective_endpoint_identifier,
            "example-business-en",
        )

    def test_rss_feed_config_is_immutable(self) -> None:
        feed = RSS_FEEDS[0]

        with self.assertRaises(FrozenInstanceError):
            setattr(
                feed,
                "name",
                "Changed name",
            )


if __name__ == "__main__":
    unittest.main()
