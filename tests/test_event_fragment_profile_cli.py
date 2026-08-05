import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.event_fragment_profiles import (
    EventFragmentProfileExclusion,
    EventFragmentProfileSignal,
    ProfileExclusionReason,
)
from argus.event_observations import EventObservationType
from argus.interface.cli import app
from argus.services.event_fragment_profile_service import (
    EventFragmentProfileReport,
    EventFragmentProfileView,
)


class EventFragmentProfileCliTests(unittest.TestCase):
    @patch("argus.interface.cli.profile_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_compact_profile_and_optional_exclusion_details(
            self,
            upgrade_database,
            configure_logging,
            profile_event_fragments,
    ) -> None:
        profile_event_fragments.return_value = EventFragmentProfileReport(
            document_version_id=34,
            event_observation_artifact_id=911,
            event_fragment_profile_artifact_id=None,
            profile_method="deterministic-event-fragment-profile",
            profile_method_version="2",
            persisted=False,
            profiles=(EventFragmentProfileView(
                event_fragment_id=1,
                signals=(EventFragmentProfileSignal(
                    observation_type=EventObservationType.PLACE_MENTION,
                    normalized_value="gaza",
                    observation_ids=(1, 3),
                    surface_forms=("Gaza",),
                    first_start_char=0,
                    last_end_char=24,
                    rationale="Grouped exact value.",
                ),),
                exclusions=(EventFragmentProfileExclusion(
                    observation_id=2,
                    observation_type=EventObservationType.ACTION_CANDIDATE,
                    normalized_value="say",
                    reason=ProfileExclusionReason.GENERIC_ACTION,
                    rationale="Generic action.",
                ),),
            ),),
            quality_limitations=("Not verified facts.",),
        )

        result = CliRunner().invoke(app, [
            "profile-event-fragments",
            "--document-version-id", "34",
            "--event-observation-artifact-id", "911",
            "--show-exclusions",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        profile_event_fragments.assert_called_once_with(
            document_version_id=34,
            event_observation_artifact_id=911,
            persist=False,
        )
        self.assertIn("raw_observations=3", result.output)
        self.assertIn("retained_occurrences=2", result.output)
        self.assertIn("signals=1 excluded=1", result.output)
        self.assertIn("observation_ids=1,3", result.output)
        self.assertIn("extent=0:24", result.output)
        self.assertIn("excluded_observation_id=2", result.output)
        self.assertIn("reason='generic_action'", result.output)
        self.assertIn("event_assignments=0", result.output)

    @patch("argus.interface.cli.profile_event_fragments")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_reports_selection_errors_without_traceback(
            self,
            upgrade_database,
            configure_logging,
            profile_event_fragments,
    ) -> None:
        profile_event_fragments.side_effect = ValueError(
            "Several observation artifacts exist."
        )

        result = CliRunner().invoke(app, [
            "profile-event-fragments",
            "--document-version-id", "34",
        ])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Several observation artifacts exist", result.output)
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
