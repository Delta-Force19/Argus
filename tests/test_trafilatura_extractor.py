import unittest

from argus.extraction import TextExtractionError
from argus.extractors.trafilatura_extractor import TrafilaturaTextExtractor


class TrafilaturaTextExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = TrafilaturaTextExtractor()

    def test_extracts_normalized_text_from_html_bytes(self) -> None:
        result = self.extractor.extract(
            b"<html><body><article><h1>Title</h1><p>First paragraph.</p>"
            b"<p>Second paragraph.</p></article></body></html>",
            media_type="Text/HTML; charset=UTF-8",
        )

        self.assertIn("Title", result.text)
        self.assertIn("First paragraph.", result.text)
        self.assertIn("Second paragraph.", result.text)
        self.assertTrue(result.quality_limitations)

    def test_rejects_unsupported_media_type(self) -> None:
        with self.assertRaisesRegex(TextExtractionError, "media type"):
            self.extractor.extract(b"%PDF", media_type="application/pdf")

    def test_rejects_document_without_extractable_text(self) -> None:
        with self.assertRaises(TextExtractionError):
            self.extractor.extract(b"<html></html>", media_type="text/html")


if __name__ == "__main__":
    unittest.main()
