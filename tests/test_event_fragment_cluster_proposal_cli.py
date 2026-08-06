import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.event_fragment_cluster_proposals import (
    CandidateGraphComponent,
    ClusterBlockingPair,
    ClusterComponentStatus,
    ClusterSupportingPair,
    EventFragmentClusterProposal,
)
from argus.event_fragment_pair_candidates import FragmentPairStatus
from argus.event_observations import EventObservationType
from argus.interface.cli import app
from argus.services.event_fragment_cluster_proposal_service import (
    EventFragmentClusterProposalReport,
)


class EventFragmentClusterProposalCliTests(unittest.TestCase):
    @patch("argus.interface.cli.propose_event_fragment_clusters")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_proposals_components_and_optional_blockers(
            self, upgrade_database, configure_logging, propose_clusters):
        supporting = ClusterSupportingPair(
            1, 3, (EventObservationType.PLACE_MENTION,), 3
        )
        blocker = ClusterBlockingPair(
            1, 2, FragmentPairStatus.WEAK, "One shared action."
        )
        propose_clusters.return_value = EventFragmentClusterProposalReport(
            document_version_id=34,
            fragment_pair_candidate_artifact_id=914,
            cluster_proposal_artifact_id=None,
            method="cluster-method",
            method_version="1",
            persisted=False,
            proposals=(EventFragmentClusterProposal(
                1, (1, 3), (supporting,),
                (EventObservationType.PLACE_MENTION,), 3, "Maximal clique.",
            ),),
            components=(CandidateGraphComponent(
                (1, 2, 3), ClusterComponentStatus.AMBIGUOUS, (1,), 2,
                (blocker,), "Not transitively merged.",
            ),),
            quality_limitations=("No event identity.",),
        )

        result = CliRunner().invoke(app, [
            "propose-event-fragment-clusters",
            "--document-version-id", "34",
            "--fragment-pair-candidate-artifact-id", "914",
            "--show-blockers",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("proposals=1 components=1", result.output)
        self.assertIn("ambiguous=1", result.output)
        self.assertIn("event_fragment_ids=1,3", result.output)
        self.assertIn("left_event_fragment_id=1", result.output)
        self.assertIn("events_created=0 event_assignments=0", result.output)


if __name__ == "__main__":
    unittest.main()
