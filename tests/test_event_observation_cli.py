import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.event_observations import EventObservationType
from argus.interface.cli import app
from argus.services.event_observation_extraction_service import (
    EventObservationExtractionReport,
    EventObservationView,
)


class EventObservationCliTests(unittest.TestCase):
    @patch("argus.interface.cli.extract_event_observations")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_previews_source_located_candidates_without_event_assignment(
            self,
            upgrade_database,
            configure_logging,
            extract_event_observations,
    ) -> None:
        extract_event_observations.return_value = (
            EventObservationExtractionReport(
                document_version_id=34,
                text_derived_artifact_id=910,
                event_observation_artifact_id=None,
                fragment_method="deterministic-cue-gap-segmentation",
                fragment_method_version="1",
                extraction_method="spacy-event-observations",
                extraction_method_version="en_core_web_sm@3.8.0",
                persisted=False,
                items=(EventObservationView(
                    event_observation_id=None,
                    event_fragment_id=4,
                    observation_type=EventObservationType.PLACE_MENTION,
                    source_label="GPE",
                    surface_text="Cyprus",
                    normalized_value="cyprus",
                    start_char=2400,
                    end_char=2406,
                    rationale="Named-entity signal.",
                ),),
                quality_limitations=("Not a verified event role.",),
                fragment_ids=(1, 2, 3, 4, 5),
            )
        )

        result = CliRunner().invoke(app, [
            "extract-event-observations",
            "--document-version-id", "34",
            "--text-artifact-id", "910",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        extract_event_observations.assert_called_once_with(
            document_version_id=34,
            text_derived_artifact_id=910,
            fragment_method=None,
            fragment_method_version=None,
            persist=False,
        )
        self.assertIn("fragments=5 observations=1 persisted=false", result.output)
        self.assertIn("place_mention=1", result.output)
        self.assertIn("event_fragment_id=4", result.output)
        self.assertIn("span=2400:2406", result.output)
        self.assertIn('text="Cyprus"', result.output)
        self.assertIn("event_assignment=none", result.output)

    @patch("argus.interface.cli.extract_event_observations")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_reports_selection_errors_without_traceback(
            self,
            upgrade_database,
            configure_logging,
            extract_event_observations,
    ) -> None:
        extract_event_observations.side_effect = ValueError(
            "Several fragment methods exist."
        )

        result = CliRunner().invoke(app, [
            "extract-event-observations",
            "--document-version-id", "34",
        ])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Several fragment methods exist", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
