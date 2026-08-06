from dataclasses import dataclass
from enum import Enum


class ProposalReviewStatus(str, Enum):
    """Explicit human disposition of one cluster proposal."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"


class ComponentReviewStatus(str, Enum):
    """Effective review state of one candidate-graph component."""

    RESOLVED = "resolved"
    REJECTED = "rejected"
    PRESERVED_AMBIGUITY = "preserved_ambiguity"
    PENDING = "pending"
    ISOLATED = "isolated"


@dataclass(frozen=True, slots=True)
class ReviewedClusterProposal:
    proposal_id: int
    event_fragment_ids: tuple[int, ...]
    status: ProposalReviewStatus


@dataclass(frozen=True, slots=True)
class ReviewedClusterComponent:
    event_fragment_ids: tuple[int, ...]
    proposal_ids: tuple[int, ...]
    status: ComponentReviewStatus
    accepted_proposal_id: int | None
    rationale: str


@dataclass(frozen=True, slots=True)
class EventFragmentClusterReviewResult:
    proposals: tuple[ReviewedClusterProposal, ...]
    components: tuple[ReviewedClusterComponent, ...]
