import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.event_fragment_segmentation_service import (
    DocumentTextInspection,
    EventFragmentProposal,
    EventFragmentSegmentationReport,
    TextBlockView,
)
from argus.services.event_text_readiness_service import (
    EventTextReadiness,
    EventTextReadinessStatus,
)
from argus.services.transcript_timeline_service import (
    TranscriptCueTimelineItem,
    TranscriptTimelineReport,
)


READY = EventTextReadiness(
    status=EventTextReadinessStatus.READY,
    ready_for_event_analysis=True,
    reasons=(),
    limitations=(),
)


class EventFragmentSegmentationCliTests(unittest.TestCase):
    @patch("argus.interface.cli.inspect_document_text")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_inspect_prints_exact_offsets_and_json_text(
            self,
            upgrade_database,
            configure_logging,
            inspect_document_text,
    ) -> None:
        inspect_document_text.return_value = DocumentTextInspection(
            document_version_id=34,
            text_derived_artifact_id=12,
            character_count=18,
            text_hash="a" * 64,
            blocks=(
                TextBlockView(
                    block_index=1,
                    start_char=0,
                    end_char=18,
                    text_hash="b" * 64,
                    text="A quoted \"heading\"",
                    heading_candidate=True,
                ),
            ),
            event_text_readiness=READY,
        )

        result = CliRunner().invoke(
            app,
            ["inspect-document-text", "--document-version-id", "34"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        inspect_document_text.assert_called_once_with(
            document_version_id=34,
            text_derived_artifact_id=None,
        )
        self.assertIn("text_artifact_id=12", result.output)
        self.assertIn("span=0:18", result.output)
        self.assertIn("heading_candidate=true", result.output)
        self.assertIn(r'text="A quoted \"heading\""', result.output)

    @patch("argus.interface.cli.segment_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_segmentation_previews_without_persistence(
            self,
            upgrade_database,
            configure_logging,
            segment_event_fragments,
    ) -> None:
        segment_event_fragments.return_value = EventFragmentSegmentationReport(
            document_version_id=34,
            text_derived_artifact_id=12,
            method="deterministic-heading-paragraph-segmentation",
            method_version="1",
            persisted=False,
            boundary_basis="heading-like-paragraphs",
            items=(
                EventFragmentProposal(
                    event_fragment_id=None,
                    start_char=10,
                    end_char=42,
                    text_hash="c" * 64,
                    rationale="Structural fragment 1/1.",
                    quality_limitations=("Not an event decision.",),
                ),
            ),
            event_text_readiness=READY,
        )

        result = CliRunner().invoke(
            app,
            ["segment-event-fragments", "--document-version-id", "34"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        segment_event_fragments.assert_called_once_with(
            document_version_id=34,
            text_derived_artifact_id=None,
            persist=False,
        )
        self.assertIn("fragments=1 persisted=false", result.output)
        self.assertIn("event_fragment_id=none", result.output)
        self.assertIn("event_assignments=0", result.output)

    @patch("argus.interface.cli.inspect_transcript_timeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_transcript_timeline_prints_offsets_timing_and_suppression(
            self,
            upgrade_database,
            configure_logging,
            inspect_transcript_timeline,
    ) -> None:
        inspect_transcript_timeline.return_value = TranscriptTimelineReport(
            document_version_id=34,
            transcript_artifact_id=910,
            transcript_acquisition_id=6,
            raw_artifact_id=227,
            character_count=9,
            text_hash="a" * 64,
            cue_provenance_schema_version="1",
            time_unit="milliseconds",
            items=(
                TranscriptCueTimelineItem(
                    cue_index=1,
                    source_block_index=2,
                    source_text_hash="b" * 64,
                    start_ms=0,
                    end_ms=1000,
                    gap_before_ms=None,
                    normalized_cue_text="One.",
                    output_start_char=0,
                    output_end_char=4,
                    output_text="One.",
                    removed_prefix_word_count=0,
                    removed_internal_overlap_word_count=0,
                    suppression_reason=None,
                ),
                TranscriptCueTimelineItem(
                    cue_index=2,
                    source_block_index=3,
                    source_text_hash="c" * 64,
                    start_ms=1000,
                    end_ms=1010,
                    gap_before_ms=None,
                    normalized_cue_text="One.",
                    output_start_char=None,
                    output_end_char=None,
                    output_text="",
                    removed_prefix_word_count=1,
                    removed_internal_overlap_word_count=0,
                    suppression_reason="technical_relay",
                ),
            ),
        )

        result = CliRunner().invoke(app, [
            "inspect-transcript-timeline",
            "--document-version-id", "34",
            "--text-artifact-id", "910",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        inspect_transcript_timeline.assert_called_once_with(
            document_version_id=34,
            transcript_artifact_id=910,
        )
        self.assertIn("contributing_cues=1 suppressed_cues=1", result.output)
        self.assertIn("shown_cues=2 shown_range=1:2", result.output)
        self.assertIn("time_ms=0:1000", result.output)
        self.assertIn("output_span=0:4", result.output)
        self.assertIn("suppression='technical_relay'", result.output)

    @patch("argus.interface.cli.inspect_transcript_timeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_transcript_timeline_rejects_start_beyond_available_cues(
            self,
            upgrade_database,
            configure_logging,
            inspect_transcript_timeline,
    ) -> None:
        inspect_transcript_timeline.return_value = TranscriptTimelineReport(
            document_version_id=34,
            transcript_artifact_id=910,
            transcript_acquisition_id=6,
            raw_artifact_id=227,
            character_count=4,
            text_hash="a" * 64,
            cue_provenance_schema_version="1",
            time_unit="milliseconds",
            items=(
                TranscriptCueTimelineItem(
                    cue_index=1,
                    source_block_index=2,
                    source_text_hash="b" * 64,
                    start_ms=0,
                    end_ms=1000,
                    gap_before_ms=None,
                    normalized_cue_text="One.",
                    output_start_char=0,
                    output_end_char=4,
                    output_text="One.",
                    removed_prefix_word_count=0,
                    removed_internal_overlap_word_count=0,
                    suppression_reason=None,
                ),
            ),
        )

        result = CliRunner().invoke(app, [
            "inspect-transcript-timeline",
            "--document-version-id", "34",
            "--text-artifact-id", "910",
            "--start-cue", "2",
        ])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("exceeds the available cue count: 1", result.output)
        self.assertNotIn("Traceback", result.output)

    @patch("argus.interface.cli.segment_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_segmentation_passes_explicit_persistence_and_artifact(
            self,
            upgrade_database,
            configure_logging,
            segment_event_fragments,
    ) -> None:
        segment_event_fragments.side_effect = ValueError(
            "Derived text artifact does not exist: 999."
        )

        result = CliRunner().invoke(
            app,
            [
                "segment-event-fragments",
                "--document-version-id",
                "34",
                "--text-artifact-id",
                "999",
                "--persist",
            ],
        )

        self.assertEqual(result.exit_code, 2)
        segment_event_fragments.assert_called_once_with(
            document_version_id=34,
            text_derived_artifact_id=999,
            persist=True,
        )
        self.assertIn("does not exist", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
