import unittest

from argus.analysis.deterministic_event_fragment_pair_comparator import (
    DeterministicEventFragmentPairComparator,
)
from argus.event_fragment_pair_candidates import (
    FragmentPairStatus,
    FragmentProfile,
    FragmentProfileSignal,
)
from argus.event_observations import EventObservationType


def _signal(observation_type, value, observation_id):
    return FragmentProfileSignal(
        observation_type=observation_type,
        normalized_value=value,
        observation_ids=(observation_id,),
    )


class DeterministicEventFragmentPairComparatorTests(unittest.TestCase):
    def test_audits_every_pair_and_requires_independent_evidence_types(self):
        profiles = (
            FragmentProfile(1, (
                _signal(EventObservationType.PLACE_MENTION, "gaza", 1),
                _signal(EventObservationType.ACTION_CANDIDATE, "attack", 2),
            )),
            FragmentProfile(2, (
                _signal(EventObservationType.PLACE_MENTION, "gaza", 3),
                _signal(EventObservationType.ACTION_CANDIDATE, "attack", 4),
            )),
            FragmentProfile(3, (
                _signal(EventObservationType.PLACE_MENTION, "gaza", 5),
            )),
        )

        result = DeterministicEventFragmentPairComparator().compare(profiles)

        self.assertEqual(len(result.pairs), 3)
        self.assertEqual(result.pairs[0].status, FragmentPairStatus.CANDIDATE)
        self.assertEqual(result.pairs[0].evidence_points, 5)
        self.assertEqual(result.pairs[1].status, FragmentPairStatus.WEAK)
        self.assertEqual(result.pairs[2].status, FragmentPairStatus.WEAK)

    def test_no_overlap_is_insufficient_not_negative_evidence(self):
        result = DeterministicEventFragmentPairComparator().compare((
            FragmentProfile(1, (
                _signal(EventObservationType.PLACE_MENTION, "gaza", 1),
            )),
            FragmentProfile(2, (
                _signal(EventObservationType.PLACE_MENTION, "cyprus", 2),
            )),
        ))

        pair = result.pairs[0]
        self.assertEqual(pair.status, FragmentPairStatus.INSUFFICIENT)
        self.assertEqual(pair.matches, ())
        self.assertIn("No exact", pair.rationale)


if __name__ == "__main__":
    unittest.main()
