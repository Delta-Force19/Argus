import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.event_fragment_cluster_reviews import (
    ComponentReviewStatus,
    ProposalReviewStatus,
    ReviewedClusterComponent,
    ReviewedClusterProposal,
)
from argus.interface.cli import app
from argus.services.event_fragment_cluster_review_service import (
    EventFragmentClusterReviewReport,
)


class EventFragmentClusterReviewCliTests(unittest.TestCase):
    @patch("argus.interface.cli.review_event_fragment_clusters")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_explicit_review_without_event_creation(
            self, upgrade_database, configure_logging, review_clusters):
        review_clusters.return_value = EventFragmentClusterReviewReport(
            document_version_id=34,
            cluster_proposal_artifact_id=915,
            cluster_review_artifact_id=916,
            persisted=True,
            reviewer="Victor",
            reason="Keep ambiguity.",
            proposals=(
                ReviewedClusterProposal(1, (1, 3), ProposalReviewStatus.PENDING),
                ReviewedClusterProposal(2, (2, 3), ProposalReviewStatus.PENDING),
            ),
            components=(
                ReviewedClusterComponent(
                    (1, 2, 3), (1, 2),
                    ComponentReviewStatus.PRESERVED_AMBIGUITY,
                    None, "Reviewer preserved alternatives.",
                ),
                ReviewedClusterComponent(
                    (4,), (), ComponentReviewStatus.ISOLATED,
                    None, "No proposal.",
                ),
            ),
        )

        result = CliRunner().invoke(app, [
            "review-event-fragment-clusters",
            "--document-version-id", "34",
            "--cluster-proposal-artifact-id", "915",
            "--preserve-component-fragment-ids", "1,2,3",
            "--reviewer", "Victor",
            "--reason", "Keep ambiguity.",
            "--persist",
        ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("cluster_review_artifact_id=916", result.output)
        self.assertIn("preserved_ambiguity=1", result.output)
        self.assertIn("events_created=0 event_assignments=0", result.output)
        review_clusters.assert_called_once_with(
            document_version_id=34,
            cluster_proposal_artifact_id=915,
            accepted_proposal_ids=(),
            rejected_proposal_ids=(),
            preserved_component_fragment_ids=((1, 2, 3),),
            reviewer="Victor",
            reason="Keep ambiguity.",
            persist=True,
        )


if __name__ == "__main__":
    unittest.main()
