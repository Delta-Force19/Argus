from itertools import combinations

from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairComparisonResult,
    FragmentPairMatch,
    FragmentPairStatus,
    FragmentProfile,
)
from argus.event_observations import EventObservationType


class DeterministicEventFragmentPairComparator:
    """Compare fragment profiles without asserting event identity."""

    EVIDENCE_POINTS = {
        EventObservationType.PARTICIPANT_MENTION: 4,
        EventObservationType.PLACE_MENTION: 3,
        EventObservationType.TIME_MENTION: 2,
        EventObservationType.EVENT_MENTION: 3,
        EventObservationType.ACTION_CANDIDATE: 2,
        EventObservationType.OBJECT_CANDIDATE: 1,
    }
    CORE_TYPES = frozenset({
        EventObservationType.PARTICIPANT_MENTION,
        EventObservationType.PLACE_MENTION,
        EventObservationType.TIME_MENTION,
        EventObservationType.EVENT_MENTION,
    })
    QUALITY_LIMITATIONS = (
        "Pair candidates compare source claims, not verified event facts.",
        "Exact normalized-value matching misses aliases and paraphrases.",
        "Shared names or places can reflect topic overlap rather than one event.",
        "Candidate status is a review priority, not an event assignment.",
    )

    @property
    def method(self) -> str:
        return "deterministic-event-fragment-pair-comparison"

    @property
    def method_version(self) -> str:
        return "1"

    def compare(
            self,
            profiles: tuple[FragmentProfile, ...],
    ) -> FragmentPairComparisonResult:
        ids = [profile.event_fragment_id for profile in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Fragment profiles must have unique identifiers.")
        ordered = sorted(profiles, key=lambda item: item.event_fragment_id)
        pairs = tuple(
            self._compare_pair(left, right)
            for left, right in combinations(ordered, 2)
        )
        return FragmentPairComparisonResult(
            pairs=pairs,
            quality_limitations=self.QUALITY_LIMITATIONS,
        )

    def _compare_pair(
            self,
            left: FragmentProfile,
            right: FragmentProfile,
    ) -> FragmentPairCandidate:
        left_index = self._index(left)
        right_index = self._index(right)
        shared_keys = sorted(
            left_index.keys() & right_index.keys(),
            key=lambda item: (item[0].value, item[1]),
        )
        matches = tuple(
            FragmentPairMatch(
                observation_type=observation_type,
                normalized_value=value,
                left_observation_ids=left_index[(observation_type, value)],
                right_observation_ids=right_index[(observation_type, value)],
                evidence_points=self.EVIDENCE_POINTS[observation_type],
                rationale=(
                    "Exact normalized value occurs in both immutable fragment "
                    "profiles for the same observation type."
                ),
            )
            for observation_type, value in shared_keys
        )
        dimensions = tuple(sorted(
            {item.observation_type for item in matches},
            key=lambda item: item.value,
        ))
        has_core = bool(set(dimensions) & self.CORE_TYPES)
        if len(dimensions) >= 2 and has_core:
            status = FragmentPairStatus.CANDIDATE
            rationale = (
                "Shared evidence spans at least two observation types and "
                "includes a participant, place, time, or event mention."
            )
        elif matches:
            status = FragmentPairStatus.WEAK
            rationale = (
                "Shared evidence exists but does not satisfy the versioned "
                "multi-type candidate rule."
            )
        else:
            status = FragmentPairStatus.INSUFFICIENT
            rationale = "No exact retained profile signal is shared."
        return FragmentPairCandidate(
            left_event_fragment_id=left.event_fragment_id,
            right_event_fragment_id=right.event_fragment_id,
            status=status,
            evidence_dimensions=dimensions,
            evidence_points=sum(item.evidence_points for item in matches),
            matches=matches,
            rationale=rationale,
        )

    @staticmethod
    def _index(
            profile: FragmentProfile,
    ) -> dict[tuple[EventObservationType, str], tuple[int, ...]]:
        indexed: dict[
            tuple[EventObservationType, str], tuple[int, ...]
        ] = {}
        for signal in profile.signals:
            key = (signal.observation_type, signal.normalized_value)
            if key in indexed:
                raise ValueError("Profile contains duplicate grouped signals.")
            indexed[key] = signal.observation_ids
        return indexed
