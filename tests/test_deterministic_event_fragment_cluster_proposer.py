import unittest
from itertools import combinations

from argus.analysis.deterministic_event_fragment_cluster_proposer import (
    DeterministicEventFragmentClusterProposer,
)
from argus.event_fragment_cluster_proposals import ClusterComponentStatus
from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairStatus,
)
from argus.event_observations import EventObservationType


def pair(left, right, status):
    dimensions = (
        EventObservationType.PLACE_MENTION,
        EventObservationType.ACTION_CANDIDATE,
    ) if status is FragmentPairStatus.CANDIDATE else ()
    return FragmentPairCandidate(
        left, right, status, dimensions,
        5 if dimensions else 0, (), f"{status.value} pair.",
    )


class DeterministicEventFragmentClusterProposerTests(unittest.TestCase):
    def test_path_becomes_overlapping_cliques_not_transitive_cluster(self):
        result = DeterministicEventFragmentClusterProposer().propose((
            pair(1, 2, FragmentPairStatus.WEAK),
            pair(1, 3, FragmentPairStatus.CANDIDATE),
            pair(1, 4, FragmentPairStatus.INSUFFICIENT),
            pair(2, 3, FragmentPairStatus.CANDIDATE),
            pair(2, 4, FragmentPairStatus.INSUFFICIENT),
            pair(3, 4, FragmentPairStatus.INSUFFICIENT),
        ))

        self.assertEqual(
            [item.event_fragment_ids for item in result.proposals],
            [(1, 3), (2, 3)],
        )
        self.assertEqual(result.components[0].event_fragment_ids, (1, 2, 3))
        self.assertEqual(
            result.components[0].status, ClusterComponentStatus.AMBIGUOUS
        )
        self.assertEqual(result.components[0].proposal_ids, (1, 2))
        self.assertEqual(
            result.components[0].blocking_pairs[0].status,
            FragmentPairStatus.WEAK,
        )
        self.assertEqual(result.components[1].event_fragment_ids, (4,))
        self.assertEqual(
            result.components[1].status, ClusterComponentStatus.ISOLATED
        )

    def test_complete_component_becomes_one_maximal_proposal(self):
        result = DeterministicEventFragmentClusterProposer().propose(tuple(
            pair(left, right, FragmentPairStatus.CANDIDATE)
            for left, right in combinations((1, 2, 3), 2)
        ))

        self.assertEqual(len(result.proposals), 1)
        self.assertEqual(result.proposals[0].event_fragment_ids, (1, 2, 3))
        self.assertEqual(len(result.proposals[0].supporting_pairs), 3)
        self.assertEqual(
            result.components[0].status, ClusterComponentStatus.COHERENT
        )

    def test_rejects_non_exhaustive_pair_audit(self):
        with self.assertRaisesRegex(ValueError, "every unordered pair"):
            DeterministicEventFragmentClusterProposer().propose((
                pair(1, 2, FragmentPairStatus.CANDIDATE),
                pair(2, 3, FragmentPairStatus.CANDIDATE),
            ))


if __name__ == "__main__":
    unittest.main()
