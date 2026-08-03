import unittest

from argus.documents import DerivedArtifactType
from argus.services.event_text_readiness_service import (
    EventTextReadinessStatus,
    assess_event_text_readiness,
)


class EventTextReadinessServiceTests(unittest.TestCase):
    def test_blocks_extracted_html_from_video_page(self) -> None:
        result = assess_event_text_readiness(
            identifier_scheme="uri",
            identifier_value=(
                "https://www.euronews.com/video/2026/07/26/bulletin"
            ),
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            text="Generic bulletin description.",
        )

        self.assertEqual(result.status, EventTextReadinessStatus.BLOCKED)
        self.assertFalse(result.ready_for_event_analysis)
        self.assertIn("not a transcript", result.reasons[0])

    def test_accepts_transcript_from_video_page(self) -> None:
        result = assess_event_text_readiness(
            identifier_scheme="url",
            identifier_value="https://example.test/video/42",
            artifact_type=DerivedArtifactType.TRANSCRIPT,
            text="Spoken report text.",
        )

        self.assertEqual(result.status, EventTextReadinessStatus.READY)
        self.assertTrue(result.ready_for_event_analysis)

    def test_short_article_is_warned_but_not_blocked(self) -> None:
        result = assess_event_text_readiness(
            identifier_scheme="uri",
            identifier_value="https://example.test/news/brief",
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            text="A short but potentially complete news brief.",
        )

        self.assertTrue(result.ready_for_event_analysis)
        self.assertTrue(result.limitations)


if __name__ == "__main__":
    unittest.main()
