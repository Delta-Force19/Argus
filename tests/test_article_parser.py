import unittest
from unittest.mock import Mock, patch

from argus.parsers.article_parser import extract_article_text


class ArticleParserTests(unittest.TestCase):
    @patch("argus.parsers.article_parser.trafilatura.extract")
    def test_uses_primary_download_when_extraction_is_readable(
            self,
            extract,
    ) -> None:
        extract.return_value = "A readable article body."
        fallback = Mock()

        result = extract_article_text(
            "https://example.test/article",
            downloader=lambda _: "<html>primary</html>",
            fallback_downloader=fallback,
        )

        self.assertEqual(result, "A readable article body.")
        fallback.assert_not_called()

    @patch("argus.parsers.article_parser.trafilatura.extract")
    def test_retries_corrupted_download_without_brotli(
            self,
            extract,
    ) -> None:
        extract.side_effect = (
            "\ufffd\ufffd6\ufffdk\ufffd" * 100,
            "Recovered article body.",
        )
        fallback = Mock(
            return_value="<html>fallback</html>"
        )

        result = extract_article_text(
            "https://example.test/article",
            downloader=lambda _: "corrupted",
            fallback_downloader=fallback,
        )

        self.assertEqual(result, "Recovered article body.")
        fallback.assert_called_once_with(
            "https://example.test/article"
        )

    @patch("argus.parsers.article_parser.trafilatura.extract")
    def test_returns_none_when_both_extractions_are_corrupted(
            self,
            extract,
    ) -> None:
        extract.return_value = "\ufffd\ufffd6\ufffdk\ufffd" * 100

        result = extract_article_text(
            "https://example.test/article",
            downloader=lambda _: "corrupted",
            fallback_downloader=lambda _: "also corrupted",
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
