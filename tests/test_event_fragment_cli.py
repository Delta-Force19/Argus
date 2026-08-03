import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.event_fragment_service import (
    EventFragmentReport,
    EventFragmentView,
)


class EventFragmentCliTests(unittest.TestCase):
    @patch("argus.interface.cli.get_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_candidates_without_event_assignment(
            self,
            upgrade_database,
            configure_logging,
            get_event_fragments,
    ) -> None:
        get_event_fragments.return_value = EventFragmentReport(
            document_version_id=34,
            items=(
                EventFragmentView(
                    event_fragment_id=7,
                    document_version_id=34,
                    text_derived_artifact_id=9,
                    start_char=10,
                    end_char=42,
                    text_hash="a" * 64,
                    method="manual",
                    method_version="1",
                    created_by="reviewer",
                    rationale="One bulletin item.",
                    quality_limitations=("Boundary is provisional.",),
                ),
            ),
        )

        result = CliRunner().invoke(
            app,
            ["event-fragments", "--document-version-id", "34"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_event_fragments.assert_called_once_with(document_version_id=34)
        self.assertIn(
            "document_version_id=34 event_fragments=1 "
            "event_assignments=0",
            result.output,
        )
        self.assertIn("span=10:42", result.output)
        self.assertIn("event_assignment=none", result.output)
        self.assertIn("limitation='Boundary is provisional.'", result.output)

    @patch("argus.interface.cli.get_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_reports_missing_version_without_traceback(
            self,
            upgrade_database,
            configure_logging,
            get_event_fragments,
    ) -> None:
        get_event_fragments.side_effect = ValueError(
            "Document version does not exist: 999."
        )

        result = CliRunner().invoke(
            app,
            ["event-fragments", "--document-version-id", "999"],
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Document version does not exist: 999.", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
