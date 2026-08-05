from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class EventObservationType(str, Enum):
    """Narrow source-level signal that may help reconstruct an event."""

    PARTICIPANT_MENTION = "participant_mention"
    PLACE_MENTION = "place_mention"
    TIME_MENTION = "time_mention"
    EVENT_MENTION = "event_mention"
    ACTION_CANDIDATE = "action_candidate"
    OBJECT_CANDIDATE = "object_candidate"


@dataclass(frozen=True, slots=True)
class ExtractedEventObservation:
    """One model observation using offsets relative to its input fragment."""

    observation_type: EventObservationType
    source_label: str
    surface_text: str
    normalized_value: str
    start_char: int
    end_char: int
    rationale: str

    def __post_init__(self) -> None:
        if not isinstance(self.observation_type, EventObservationType):
            raise ValueError(
                "observation_type must be an EventObservationType."
            )
        if not self.source_label.strip():
            raise ValueError("source_label must not be blank.")
        if not self.surface_text:
            raise ValueError("surface_text must not be empty.")
        if not self.normalized_value:
            raise ValueError("normalized_value must not be empty.")
        if self.start_char < 0:
            raise ValueError("start_char must not be negative.")
        if self.end_char <= self.start_char:
            raise ValueError("end_char must follow start_char.")
        if not self.rationale.strip():
            raise ValueError("rationale must not be blank.")


@dataclass(frozen=True, slots=True)
class EventObservationExtractionResult:
    """Ordered observations and explicit limitations from one fragment."""

    observations: tuple[ExtractedEventObservation, ...]
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not value.strip() for value in self.quality_limitations):
            raise ValueError("quality_limitations must not contain blanks.")


@runtime_checkable
class EventObservationExtractor(Protocol):
    """Extract narrow signals without asserting an event or factual roles."""

    @property
    def method(self) -> str:
        ...

    def method_version(self, language: str) -> str:
        ...

    def extract(
            self,
            text: str,
            *,
            language: str,
    ) -> EventObservationExtractionResult:
        ...
