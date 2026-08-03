import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.transcript_ingestion_service import TranscriptIngestionResult
from argus.services.youtube_transcript_ingestion_service import (
    YouTubeTranscriptIngestionResult,
)
from argus.transcript_sources.youtube import (
    YouTubeTranscriptCatalog,
    YouTubeTranscriptTrack,
)
from argus.transcripts import TranscriptFormat, TranscriptKind


VIDEO_ID = "dcmdgYtPeTg"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _track():
    return YouTubeTranscriptTrack(
        track_id="en",
        name="English",
        transcript_kind=TranscriptKind.PUBLISHER_PROVIDED,
        transcript_format=TranscriptFormat.WEBVTT,
        media_type="text/vtt; charset=utf-8",
        location="https://captions.test/en.vtt",
    )


class YouTubeTranscriptCliTests(unittest.TestCase):
    @patch("argus.interface.cli.YouTubeTranscriptSource")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_lists_tracks_without_signed_locations(
            self,
            upgrade_database,
            configure_logging,
            source_type,
    ) -> None:
        source_type.return_value.catalog.return_value = (
            YouTubeTranscriptCatalog(
                requested_location=VIDEO_URL,
                canonical_location=VIDEO_URL,
                video_id=VIDEO_ID,
                title="Latest news bulletin",
                provider="yt-dlp/youtube",
                provider_version="2026.7.4",
                tracks=(_track(),),
            )
        )

        result = CliRunner().invoke(app, [
            "youtube-transcript-tracks",
            "--youtube-url", VIDEO_URL,
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("track_id='en'", result.output)
        self.assertIn("publisher_provided", result.output)
        self.assertNotIn("captions.test", result.output)

    @patch("argus.interface.cli.ingest_youtube_transcript")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_ingests_exact_track_with_explicit_cross_location_flag(
            self,
            upgrade_database,
            configure_logging,
            ingest,
    ) -> None:
        ingest.return_value = YouTubeTranscriptIngestionResult(
            ingestion=TranscriptIngestionResult(
                document_version_id=34,
                transcript_acquisition_id=50,
                raw_artifact_id=51,
                transcript_artifact_id=52,
                raw_content_hash="a" * 64,
                text_content_hash="b" * 64,
                character_count=4200,
                language="en",
                transcript_kind=TranscriptKind.PUBLISHER_PROVIDED,
                transcript_format=TranscriptFormat.WEBVTT,
                quality_limitations=("Timing omitted.",),
            ),
            requested_location=VIDEO_URL,
            resolved_location="https://captions.test/en.vtt",
            video_id=VIDEO_ID,
            track_id="en",
            title="Latest news bulletin",
            cross_location=True,
        )

        result = CliRunner().invoke(app, [
            "ingest-youtube-transcript",
            "--document-version-id", "34",
            "--youtube-url", VIDEO_URL,
            "--track-id", "en",
            "--allow-cross-location",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        ingest.assert_called_once_with(
            document_version_id=34,
            youtube_url=VIDEO_URL,
            track_id="en",
            allow_auto_generated=False,
            allow_cross_location=True,
        )
        self.assertIn("cross_location=true", result.output)
        self.assertIn("transcript_artifact_id=52", result.output)

    @patch("argus.interface.cli.ingest_youtube_transcript")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_reports_provider_failure_without_traceback(
            self,
            upgrade_database,
            configure_logging,
            ingest,
    ) -> None:
        ingest.side_effect = ValueError("No supported WebVTT track.")

        result = CliRunner().invoke(app, [
            "ingest-youtube-transcript",
            "--document-version-id", "34",
            "--youtube-url", VIDEO_URL,
            "--track-id", "en",
        ])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No supported WebVTT track", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
