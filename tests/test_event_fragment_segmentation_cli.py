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
