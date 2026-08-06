from dataclasses import dataclass
from enum import Enum

from argus.event_fragment_pair_candidates import FragmentPairStatus
from argus.event_observations import EventObservationType


class ClusterComponentStatus(str, Enum):
    """Review state of one component in the candidate-edge graph."""

    COHERENT = "coherent"
    AMBIGUOUS = "ambiguous"
    ISOLATED = "isolated"


@dataclass(frozen=True, slots=True)
class ClusterSupportingPair:
    left_event_fragment_id: int
    right_event_fragment_id: int
    evidence_dimensions: tuple[EventObservationType, ...]
    evidence_points: int


@dataclass(frozen=True, slots=True)
class ClusterBlockingPair:
    left_event_fragment_id: int
    right_event_fragment_id: int
    status: FragmentPairStatus
    rationale: str


@dataclass(frozen=True, slots=True)
class EventFragmentClusterProposal:
    proposal_id: int
    event_fragment_ids: tuple[int, ...]
    supporting_pairs: tuple[ClusterSupportingPair, ...]
    evidence_dimensions: tuple[EventObservationType, ...]
    evidence_points: int
    rationale: str


@dataclass(frozen=True, slots=True)
class CandidateGraphComponent:
    event_fragment_ids: tuple[int, ...]
    status: ClusterComponentStatus
    proposal_ids: tuple[int, ...]
    candidate_pair_count: int
    blocking_pairs: tuple[ClusterBlockingPair, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class EventFragmentClusterProposalResult:
    proposals: tuple[EventFragmentClusterProposal, ...]
    components: tuple[CandidateGraphComponent, ...]
    quality_limitations: tuple[str, ...]
