import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairMatch,
    FragmentPairStatus,
)
from argus.event_observations import EventObservationType
from argus.interface.cli import app
from argus.services.event_fragment_pair_candidate_service import (
    FragmentPairCandidateReport,
)


class EventFragmentPairCandidateCliTests(unittest.TestCase):
    @patch("argus.interface.cli.compare_event_fragment_profiles")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_pair_decisions_and_optional_matches(
            self, upgrade_database, configure_logging, compare_profiles):
        match = FragmentPairMatch(
            EventObservationType.PLACE_MENTION,
            "gaza",
            (1,),
            (3,),
            3,
            "Exact value.",
        )
        compare_profiles.return_value = FragmentPairCandidateReport(
            document_version_id=34,
            event_fragment_profile_artifact_id=913,
            fragment_pair_candidate_artifact_id=None,
            method="pair-method",
            method_version="1",
            persisted=False,
            pairs=(FragmentPairCandidate(
                1, 2, FragmentPairStatus.WEAK,
                (EventObservationType.PLACE_MENTION,),
                3, (match,), "One dimension.",
            ),),
            quality_limitations=("Not an assignment.",),
        )

        result = CliRunner().invoke(app, [
            "compare-event-fragments",
            "--document-version-id", "34",
            "--event-fragment-profile-artifact-id", "913",
            "--show-matches",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("pairs=1 insufficient=0 weak=1 candidate=0", result.output)
        self.assertIn("value='gaza'", result.output)
        self.assertIn("event_assignments=0", result.output)


if __name__ == "__main__":
    unittest.main()
