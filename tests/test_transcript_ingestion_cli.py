from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.transcript_ingestion_service import TranscriptIngestionResult
from argus.transcripts import TranscriptFormat, TranscriptKind


class TranscriptIngestionCliTests(unittest.TestCase):
    @patch("argus.interface.cli.ingest_transcript_file")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_passes_explicit_provenance_and_prints_ids(
            self,
            upgrade_database,
            configure_logging,
            ingest_transcript_file,
    ) -> None:
        ingest_transcript_file.return_value = TranscriptIngestionResult(
            document_version_id=34,
            transcript_acquisition_id=5,
            raw_artifact_id=40,
            transcript_artifact_id=41,
            raw_content_hash="a" * 64,
            text_content_hash="b" * 64,
            character_count=1234,
            language="en",
            transcript_kind=TranscriptKind.AUTO_GENERATED,
            transcript_format=TranscriptFormat.WEBVTT,
            quality_limitations=("Timing omitted from normalized text.",),
        )
        with TemporaryDirectory() as temporary_directory:
            transcript = Path(temporary_directory) / "captions.vtt"
            transcript.write_text("WEBVTT", encoding="utf-8")
            result = CliRunner().invoke(app, [
                "ingest-transcript",
                "--document-version-id", "34",
                "--transcript-file", str(transcript),
                "--provider", "yt-dlp",
                "--provider-version", "2026.07.31",
                "--requested-location", "https://youtube.test/watch?v=42",
                "--retrieved-at", "2026-08-03T18:00:00Z",
                "--language", "en",
                "--transcript-kind", "auto_generated",
                "--transcript-format", "webvtt",
                "--media-type", "text/vtt; charset=utf-8",
                "--external-identifier", "42:en",
            ])

        self.assertEqual(result.exit_code, 0, result.output)
        ingest_transcript_file.assert_called_once_with(
            document_version_id=34,
            transcript_file=transcript,
            provider="yt-dlp",
            provider_version="2026.07.31",
            requested_location="https://youtube.test/watch?v=42",
            resolved_location=None,
            external_identifier="42:en",
            retrieved_at=datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc),
            language="en",
            transcript_kind=TranscriptKind.AUTO_GENERATED,
            transcript_format=TranscriptFormat.WEBVTT,
            media_type="text/vtt; charset=utf-8",
        )
        self.assertIn("transcript_artifact_id=41", result.output)
        self.assertIn("raw_content_hash=" + "a" * 64, result.output)

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_rejects_retrieval_time_without_timezone(
            self,
            upgrade_database,
            configure_logging,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            transcript = Path(temporary_directory) / "captions.txt"
            transcript.write_text("Text", encoding="utf-8")
            result = CliRunner().invoke(app, [
                "ingest-transcript",
                "--document-version-id", "34",
                "--transcript-file", str(transcript),
                "--provider", "manual",
                "--provider-version", "1",
                "--requested-location", "https://example.test/video/34",
                "--retrieved-at", "2026-08-03T18:00:00",
                "--language", "en",
            ])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("must include a timezone", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
