import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import httpx

from argus.acquisition import (
    AcquisitionMode,
    CandidateRecord,
    Connector,
    DiscoveryRequest,
    RetrievalOutcome,
)
from argus.collector.rss_connector import (
    HTTP_TIMEOUT,
    RSSConnector,
    RSSDiscoveryAccessRestricted,
)
from argus.config import RSSFeedConfig


class RSSConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(
            2026,
            7,
            16,
            12,
            0,
            tzinfo=timezone.utc,
        )
        self.feed = RSSFeedConfig(
            name="Example News",
            url="https://example.com/feed.xml",
            language="en",
            country="Example Country",
            source_identifier="example-news",
        )
        self.connector = RSSConnector(
            self.feed,
            clock=lambda: self.now,
        )

    def create_candidate(self) -> CandidateRecord:
        return CandidateRecord(
            connector_id=self.connector.connector_id,
            connector_version=(
                self.connector.connector_version
            ),
            location="https://example.com/article",
            discovered_at=self.now,
            source_identifier="example-news",
        )

    def test_connector_implements_contract(self) -> None:
        self.assertIsInstance(
            self.connector,
            Connector,
        )

    @patch(
        "argus.collector.rss_connector.feedparser.parse"
    )
    @patch("argus.collector.rss_connector.httpx.get")
    def test_discover_normalizes_feed_entry(
        self,
        get_mock: Mock,
        parse_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            200,
            content=b"<rss />",
            request=httpx.Request(
                "GET",
                self.feed.url,
            ),
        )
        parse_mock.return_value.entries = [
            {
                "id": "article-1",
                "title": "Energy policy changed",
                "link": "https://example.com/article",
                "published": (
                    "Thu, 16 Jul 2026 10:00:00 GMT"
                ),
            }
        ]

        candidates = self.connector.discover(
            DiscoveryRequest(
                mode=AcquisitionMode.CONTINUOUS,
            )
        )

        self.assertEqual(len(candidates), 1)

        candidate = candidates[0]

        self.assertEqual(
            candidate.connector_id,
            "rss",
        )
        self.assertEqual(
            candidate.external_identifier,
            "article-1",
        )
        self.assertEqual(
            candidate.source_identifier,
            "example-news",
        )
        self.assertEqual(
            candidate.published_at,
            datetime(
                2026,
                7,
                16,
                10,
                0,
                tzinfo=timezone.utc,
            ),
        )
        self.assertEqual(
            candidate.discovered_at,
            self.now,
        )
        get_mock.assert_called_once_with(
            self.feed.url,
            follow_redirects=True,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Argus/0.1.1",
            },
        )
        self.assertEqual(HTTP_TIMEOUT.connect, 5.0)
        self.assertEqual(HTTP_TIMEOUT.read, 10.0)
        parse_mock.assert_called_once_with(b"<rss />")

    @patch(
        "argus.collector.rss_connector.feedparser.parse"
    )
    @patch("argus.collector.rss_connector.httpx.get")
    def test_discover_applies_query_and_limit(
        self,
        get_mock: Mock,
        parse_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            200,
            content=b"<rss />",
            request=httpx.Request(
                "GET",
                self.feed.url,
            ),
        )
        parse_mock.return_value.entries = [
            {
                "title": "Unrelated article",
                "link": "https://example.com/1",
            },
            {
                "title": "Energy policy one",
                "link": "https://example.com/2",
            },
            {
                "title": "Energy policy two",
                "link": "https://example.com/3",
            },
        ]

        candidates = self.connector.discover(
            DiscoveryRequest(
                mode=AcquisitionMode.INVESTIGATION,
                query="energy policy",
                limit=1,
            )
        )

        self.assertEqual(
            [candidate.location for candidate in candidates],
            ["https://example.com/2"],
        )

    @patch(
        "argus.collector.rss_connector.feedparser.parse"
    )
    @patch("argus.collector.rss_connector.httpx.get")
    def test_discover_normalizes_blank_optional_metadata(
        self,
        get_mock: Mock,
        parse_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            200,
            content=b"<rss />",
            request=httpx.Request(
                "GET",
                self.feed.url,
            ),
        )
        parse_mock.return_value.entries = [
            {
                "id": "   ",
                "title": "   ",
                "link": "https://example.com/article",
            }
        ]

        candidates = self.connector.discover(
            DiscoveryRequest(
                mode=AcquisitionMode.CONTINUOUS,
            )
        )

        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0].title)
        self.assertEqual(
            candidates[0].external_identifier,
            "https://example.com/article",
        )

    @patch("argus.collector.rss_connector.feedparser.parse")
    @patch("argus.collector.rss_connector.httpx.get")
    def test_discover_rejects_http_error_before_parsing(
        self,
        get_mock: Mock,
        parse_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            504,
            request=httpx.Request(
                "GET",
                self.feed.url,
            ),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            self.connector.discover(
                DiscoveryRequest(
                    mode=AcquisitionMode.CONTINUOUS,
                )
            )

        parse_mock.assert_not_called()

    @patch("argus.collector.rss_connector.feedparser.parse")
    @patch("argus.collector.rss_connector.httpx.get")
    def test_discover_classifies_access_restriction(
        self,
        get_mock: Mock,
        parse_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            403,
            request=httpx.Request(
                "GET",
                self.feed.url,
            ),
        )

        with self.assertRaises(
            RSSDiscoveryAccessRestricted
        ) as raised:
            self.connector.discover(
                DiscoveryRequest(
                    mode=AcquisitionMode.CONTINUOUS,
                )
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.url,
            self.feed.url,
        )
        parse_mock.assert_not_called()

    def test_discover_rejects_cursor(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "does not support cursors",
        ):
            self.connector.discover(
                DiscoveryRequest(
                    mode=AcquisitionMode.CONTINUOUS,
                    cursor="next-page",
                )
            )

    @patch("argus.collector.rss_connector.httpx.get")
    def test_retrieve_returns_successful_content(
        self,
        get_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            200,
            content=b"Article bytes",
            headers={
                "content-type": "text/html; charset=utf-8",
            },
            request=httpx.Request(
                "GET",
                "https://example.com/article",
            ),
        )

        result = self.connector.retrieve(
            self.create_candidate()
        )

        self.assertEqual(
            result.outcome,
            RetrievalOutcome.SUCCEEDED,
        )
        self.assertEqual(result.content, b"Article bytes")
        self.assertEqual(result.response_status, "200")

    @patch("argus.collector.rss_connector.httpx.get")
    def test_retrieve_distinguishes_restricted_content(
        self,
        get_mock: Mock,
    ) -> None:
        get_mock.return_value = httpx.Response(
            403,
            request=httpx.Request(
                "GET",
                "https://example.com/article",
            ),
        )

        result = self.connector.retrieve(
            self.create_candidate()
        )

        self.assertEqual(
            result.outcome,
            RetrievalOutcome.ACCESS_RESTRICTED,
        )
        self.assertIsNone(result.content)

    @patch("argus.collector.rss_connector.httpx.get")
    def test_retrieve_records_transport_failure(
        self,
        get_mock: Mock,
    ) -> None:
        get_mock.side_effect = httpx.ConnectError(
            "connection failed"
        )

        result = self.connector.retrieve(
            self.create_candidate()
        )

        self.assertEqual(
            result.outcome,
            RetrievalOutcome.FAILED,
        )
        self.assertEqual(
            result.error,
            "connection failed",
        )


if __name__ == "__main__":
    unittest.main()
