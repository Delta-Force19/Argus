from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from argus.event_observations import EventObservationType


class ProfileExclusionReason(str, Enum):
    """Stable reason why a raw observation was omitted from a profile."""

    GENERIC_ACTION = "generic_action"
    NON_LEXICAL_ACTION = "non_lexical_action"
    PRONOMINAL_OBJECT = "pronominal_object"
    VAGUE_OBJECT = "vague_object"
    OVERSIZED_OBJECT = "oversized_object"
    UNSUPPORTED_OBJECT_HEAD = "unsupported_object_head"


@dataclass(frozen=True, slots=True)
class ProfileObservation:
    """Source observation input consumed by a deterministic profiler."""

    observation_id: int
    observation_type: EventObservationType
    source_label: str
    surface_text: str
    normalized_value: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class EventFragmentProfileSignal:
    """One retained, grouped signal with traceable source occurrences."""

    observation_type: EventObservationType
    normalized_value: str
    observation_ids: tuple[int, ...]
    surface_forms: tuple[str, ...]
    first_start_char: int
    last_end_char: int
    rationale: str

    @property
    def occurrence_count(self) -> int:
        return len(self.observation_ids)


@dataclass(frozen=True, slots=True)
class EventFragmentProfileExclusion:
    """One omitted raw observation and an explicit deterministic reason."""

    observation_id: int
    observation_type: EventObservationType
    normalized_value: str
    reason: ProfileExclusionReason
    rationale: str


@dataclass(frozen=True, slots=True)
class EventFragmentProfileResult:
    signals: tuple[EventFragmentProfileSignal, ...]
    exclusions: tuple[EventFragmentProfileExclusion, ...]
    quality_limitations: tuple[str, ...] = ()


@runtime_checkable
class EventFragmentProfiler(Protocol):
    @property
    def method(self) -> str:
        ...

    @property
    def method_version(self) -> str:
        ...

    def profile(
            self,
            observations: tuple[ProfileObservation, ...],
            *,
            language: str,
    ) -> EventFragmentProfileResult:
        ...
