from dataclasses import dataclass
from enum import Enum

from argus.event_observations import EventObservationType


class FragmentPairStatus(str, Enum):
    """Strength of textual evidence for reviewing one fragment pair."""

    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class FragmentProfileSignal:
    observation_type: EventObservationType
    normalized_value: str
    observation_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FragmentProfile:
    event_fragment_id: int
    signals: tuple[FragmentProfileSignal, ...]


@dataclass(frozen=True, slots=True)
class FragmentPairMatch:
    observation_type: EventObservationType
    normalized_value: str
    left_observation_ids: tuple[int, ...]
    right_observation_ids: tuple[int, ...]
    evidence_points: int
    rationale: str


@dataclass(frozen=True, slots=True)
class FragmentPairCandidate:
    left_event_fragment_id: int
    right_event_fragment_id: int
    status: FragmentPairStatus
    evidence_dimensions: tuple[EventObservationType, ...]
    evidence_points: int
    matches: tuple[FragmentPairMatch, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class FragmentPairComparisonResult:
    pairs: tuple[FragmentPairCandidate, ...]
    quality_limitations: tuple[str, ...]
