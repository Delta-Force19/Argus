import unittest
from unittest.mock import Mock, patch

from argus.collector.rss_adapter import fetch_rss_entries
from argus.config import RSSFeedConfig
from argus.sources import SourceType


class RSSAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed = RSSFeedConfig(
            name="Test Source",
            url="https://example.com/feed.xml",
            language="en",
            country="Test Country",
            source_identifier="test-source",
            source_type=SourceType.NEWS_AGENCY,
        )

    @patch(
        "argus.collector.rss_adapter.feedparser.parse"
    )
    def test_fetch_rss_entries_uses_configured_url(
            self,
            parse_mock: Mock,
    ) -> None:
        parse_mock.return_value.entries = []

        fetch_rss_entries(self.feed)

        parse_mock.assert_called_once_with(
            self.feed.url
        )

    @patch(
        "argus.collector.rss_adapter.feedparser.parse"
    )
    def test_fetch_rss_entries_normalizes_metadata(
            self,
            parse_mock: Mock,
    ) -> None:
        parse_mock.return_value.entries = [
            {
                "title": "Test article",
                "link": "https://example.com/article",
                "published": "Mon, 13 Jul 2026 12:00:00 GMT",
            }
        ]

        entries = fetch_rss_entries(self.feed)

        self.assertEqual(
            entries,
            [
                {
                    "title": "Test article",
                    "link": "https://example.com/article",
                    "published": (
                        "Mon, 13 Jul 2026 12:00:00 GMT"
                    ),
                    "source": "Test Source",
                    "source_identifier": "test-source",
                    "source_type": SourceType.NEWS_AGENCY,
                    "language": "en",
                    "country": "Test Country",
                }
            ],
        )

    @patch(
        "argus.collector.rss_adapter.feedparser.parse"
    )
    def test_fetch_rss_entries_handles_empty_feed(
            self,
            parse_mock: Mock,
    ) -> None:
        parse_mock.return_value.entries = []

        entries = fetch_rss_entries(self.feed)

        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()