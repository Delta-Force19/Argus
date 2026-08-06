from itertools import combinations

from argus.event_fragment_cluster_proposals import (
    CandidateGraphComponent,
    ClusterBlockingPair,
    ClusterComponentStatus,
    ClusterSupportingPair,
    EventFragmentClusterProposal,
    EventFragmentClusterProposalResult,
)
from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairStatus,
)


class DeterministicEventFragmentClusterProposer:
    """Build review proposals without applying transitive event identity."""

    QUALITY_LIMITATIONS = (
        "Cluster proposals organize pair-candidate evidence; they do not "
        "prove event identity.",
        "Maximal all-candidate cliques can overlap and therefore are not a partition.",
        "Weak or insufficient pairs block clique expansion but do not prove "
        "different events.",
        "No Event or event-fragment assignment is created by this method.",
    )

    @property
    def method(self) -> str:
        return "deterministic-event-fragment-cluster-proposal"

    @property
    def method_version(self) -> str:
        return "1"

    def propose(
            self,
            pairs: tuple[FragmentPairCandidate, ...],
    ) -> EventFragmentClusterProposalResult:
        pair_index, fragment_ids = self._validate_and_index(pairs)
        candidate_edges = {
            key for key, pair in pair_index.items()
            if pair.status is FragmentPairStatus.CANDIDATE
        }
        cliques = self._maximal_candidate_cliques(fragment_ids, candidate_edges)
        proposals = tuple(
            self._proposal(index, clique, pair_index)
            for index, clique in enumerate(cliques, start=1)
        )
        components = tuple(
            self._component(component, proposals, pair_index, candidate_edges)
            for component in self._components(fragment_ids, candidate_edges)
        )
        return EventFragmentClusterProposalResult(
            proposals=proposals,
            components=components,
            quality_limitations=self.QUALITY_LIMITATIONS,
        )

    @staticmethod
    def _validate_and_index(
            pairs: tuple[FragmentPairCandidate, ...],
    ) -> tuple[dict[tuple[int, int], FragmentPairCandidate], tuple[int, ...]]:
        if not pairs:
            raise ValueError("At least one fragment pair is required.")
        indexed: dict[tuple[int, int], FragmentPairCandidate] = {}
        identifiers: set[int] = set()
        for pair in pairs:
            left = pair.left_event_fragment_id
            right = pair.right_event_fragment_id
            if left < 1 or right < 1 or left >= right:
                raise ValueError(
                    "Fragment pairs must use positive ordered identifiers."
                )
            key = (left, right)
            if key in indexed:
                raise ValueError("Fragment-pair audit contains duplicate pairs.")
            indexed[key] = pair
            identifiers.update(key)
        ordered_ids = tuple(sorted(identifiers))
        expected = set(combinations(ordered_ids, 2))
        if set(indexed) != expected:
            raise ValueError("Fragment-pair audit must contain every unordered pair.")
        return indexed, ordered_ids

    @staticmethod
    def _maximal_candidate_cliques(
            fragment_ids: tuple[int, ...],
            candidate_edges: set[tuple[int, int]],
    ) -> tuple[tuple[int, ...], ...]:
        neighbours = {identifier: set() for identifier in fragment_ids}
        for left, right in candidate_edges:
            neighbours[left].add(right)
            neighbours[right].add(left)
        maximal: list[tuple[int, ...]] = []

        def visit(current: set[int], possible: set[int], excluded: set[int]) -> None:
            if not possible and not excluded:
                if len(current) >= 2:
                    maximal.append(tuple(sorted(current)))
                return
            pivot_candidates = possible | excluded
            pivot = min(
                pivot_candidates,
                key=lambda item: (-len(possible & neighbours[item]), item),
            ) if pivot_candidates else None
            extension = possible - (neighbours[pivot] if pivot is not None else set())
            for identifier in sorted(extension):
                visit(
                    current | {identifier},
                    possible & neighbours[identifier],
                    excluded & neighbours[identifier],
                )
                possible.remove(identifier)
                excluded.add(identifier)

        visit(set(), set(fragment_ids), set())
        return tuple(sorted(maximal))

    @staticmethod
    def _proposal(
            proposal_id: int,
            members: tuple[int, ...],
            pair_index: dict[tuple[int, int], FragmentPairCandidate],
    ) -> EventFragmentClusterProposal:
        source_pairs = tuple(pair_index[key] for key in combinations(members, 2))
        supporting = tuple(ClusterSupportingPair(
            pair.left_event_fragment_id,
            pair.right_event_fragment_id,
            pair.evidence_dimensions,
            pair.evidence_points,
        ) for pair in source_pairs)
        dimensions = tuple(sorted(
            {item for pair in source_pairs for item in pair.evidence_dimensions},
            key=lambda item: item.value,
        ))
        return EventFragmentClusterProposal(
            proposal_id=proposal_id,
            event_fragment_ids=members,
            supporting_pairs=supporting,
            evidence_dimensions=dimensions,
            evidence_points=sum(pair.evidence_points for pair in source_pairs),
            rationale=(
                "Every internal fragment pair is candidate under the selected "
                "immutable pair audit, and no additional fragment can be added "
                "while preserving that rule."
            ),
        )

    @staticmethod
    def _components(
            fragment_ids: tuple[int, ...],
            candidate_edges: set[tuple[int, int]],
    ) -> tuple[tuple[int, ...], ...]:
        remaining = set(fragment_ids)
        components: list[tuple[int, ...]] = []
        while remaining:
            pending = [min(remaining)]
            reached: set[int] = set()
            while pending:
                current = pending.pop()
                if current in reached:
                    continue
                reached.add(current)
                neighbours = {
                    right if left == current else left
                    for left, right in candidate_edges
                    if left == current or right == current
                }
                pending.extend(sorted(neighbours - reached, reverse=True))
            remaining -= reached
            components.append(tuple(sorted(reached)))
        return tuple(components)

    @staticmethod
    def _component(
            members: tuple[int, ...],
            proposals: tuple[EventFragmentClusterProposal, ...],
            pair_index: dict[tuple[int, int], FragmentPairCandidate],
            candidate_edges: set[tuple[int, int]],
    ) -> CandidateGraphComponent:
        member_set = set(members)
        proposal_ids = tuple(
            item.proposal_id for item in proposals
            if set(item.event_fragment_ids) <= member_set
        )
        internal_keys = tuple(combinations(members, 2))
        blockers = tuple(ClusterBlockingPair(
            left, right, pair_index[(left, right)].status,
            pair_index[(left, right)].rationale,
        ) for left, right in internal_keys if (left, right) not in candidate_edges)
        candidate_count = len(internal_keys) - len(blockers)
        if len(members) == 1:
            status = ClusterComponentStatus.ISOLATED
            rationale = "Fragment has no candidate edge in the selected pair audit."
        elif not blockers:
            status = ClusterComponentStatus.COHERENT
            rationale = "The complete candidate component is one all-pairs proposal."
        else:
            status = ClusterComponentStatus.AMBIGUOUS
            rationale = (
                "Transitive candidate connectivity is not applied because at least "
                "one internal pair is weak or insufficient; overlapping maximal "
                "cliques remain separate alternatives."
            )
        return CandidateGraphComponent(
            event_fragment_ids=members,
            status=status,
            proposal_ids=proposal_ids,
            candidate_pair_count=candidate_count,
            blocking_pairs=blockers,
            rationale=rationale,
        )
