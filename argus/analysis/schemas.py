from dataclasses import dataclass, field
from enum import Enum


class EvidenceCategory(str, Enum):
    """Observable lexical evidence category."""

    CERTAINTY = "certainty"
    UNCERTAINTY = "uncertainty"
    FEAR = "fear"
    THREAT = "threat"


@dataclass(slots=True)
class EvidenceSpan:
    category: EvidenceCategory
    sentence: str
    matched_terms: list[str] = field(
        default_factory=list
    )


@dataclass(slots=True)
class DiscourseMetrics:
    word_count: int
    sentence_count: int
    average_sentence_length: float
    question_count: int
    exclamation_count: int

    first_person_plural_count: int
    third_person_plural_count: int

    certainty_marker_count: int
    uncertainty_marker_count: int
    fear_marker_count: int
    threat_marker_count: int

    evidence: list[EvidenceSpan] = field(
        default_factory=list
    )