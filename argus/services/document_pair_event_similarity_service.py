from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
import re

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.services.document_analysis_input_service import (
    DocumentAnalysisInputBundle,
    build_document_analysis_input,
)
from argus.services.event_text_readiness_service import (
    assess_event_text_readiness,
)


_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


@dataclass(frozen=True, slots=True)
class EventSimilarityConfiguration:
    """Explicit uncalibrated weights for document-pair evidence."""

    temporal_window_hours: float = 72.0
    minimum_lexical_tokens: int = 20
    temporal_weight: float = 0.2
    entity_weight: float = 0.5
    lexical_weight: float = 0.3


@dataclass(frozen=True, slots=True)
class EventSimilaritySignal:
    """One independently inspectable document-pair similarity signal."""

    name: str
    available: bool
    score: float | None
    configured_weight: float
    effective_weight: float
    contribution: float | None
    explanation: str


@dataclass(frozen=True, slots=True)
class DocumentPairEventSimilarity:
    """Read-only evidence for, but never a decision about, event identity."""

    left_document_id: int
    left_document_version_id: int
    right_document_id: int
    right_document_version_id: int
    combined_score: float | None
    available_weight: float
    signals: tuple[EventSimilaritySignal, ...]
    shared_entity_ids: tuple[int, ...]
    limitations: tuple[str, ...]


def get_document_pair_event_similarity(
        *,
        left_document_version_id: int,
        right_document_version_id: int,
        configuration: EventSimilarityConfiguration | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentPairEventSimilarity:
    """Load two strict analysis bundles in one session and compare them."""

    with session_factory() as session:
        left = build_document_analysis_input(
            session,
            document_version_id=left_document_version_id,
        )
        right = build_document_analysis_input(
            session,
            document_version_id=right_document_version_id,
        )
        return compare_document_pair_event_similarity(
            left,
            right,
            configuration=configuration,
        )


def compare_document_pair_event_similarity(
        left: DocumentAnalysisInputBundle,
        right: DocumentAnalysisInputBundle,
        *,
        configuration: EventSimilarityConfiguration | None = None,
) -> DocumentPairEventSimilarity:
    """Compare two ready bundles with transparent deterministic signals."""

    config = configuration or EventSimilarityConfiguration()
    _validate_configuration(config)
    _validate_pair(left, right)

    temporal = _temporal_signal(left, right, config)
    entity, shared_entity_ids = _entity_signal(left, right, config)
    lexical = _lexical_signal(left, right, config)
    raw_signals = (temporal, entity, lexical)
    available_weight = sum(
        signal.configured_weight
        for signal in raw_signals
        if signal.available
    )
    signals = tuple(
        _with_contribution(signal, available_weight=available_weight)
        for signal in raw_signals
    )
    combined_score = (
        sum(
            signal.contribution or 0.0
            for signal in signals
        )
        if available_weight > 0.0
        else None
    )

    limitations = [
        "The combined score is an uncalibrated heuristic, not a "
        "probability or same-event decision.",
        "Lexical similarity uses unweighted normalized term frequencies "
        "and may reflect shared boilerplate or topic vocabulary.",
    ]
    unavailable = [
        signal.name for signal in signals if not signal.available
    ]
    if unavailable and available_weight > 0.0:
        limitations.append(
            "Unavailable signals were omitted and remaining weights were "
            "renormalized: " + ", ".join(unavailable) + "."
        )
    elif available_weight <= 0.0:
        limitations.append(
            "No available signal had a configured weight; the combined "
            "score was withheld."
        )
    quality_limitations = sorted(
        set(left.text.quality_limitations)
        | set(right.text.quality_limitations)
    )
    limitations.extend(
        f"Input text quality limitation: {item}"
        for item in quality_limitations
    )

    return DocumentPairEventSimilarity(
        left_document_id=left.document.document_id,
        left_document_version_id=(
            left.document.document_version_id
        ),
        right_document_id=right.document.document_id,
        right_document_version_id=(
            right.document.document_version_id
        ),
        combined_score=(
            round(combined_score, 12)
            if combined_score is not None
            else None
        ),
        available_weight=round(available_weight, 12),
        signals=signals,
        shared_entity_ids=shared_entity_ids,
        limitations=tuple(limitations),
    )


def _validate_configuration(config: EventSimilarityConfiguration) -> None:
    if config.temporal_window_hours <= 0:
        raise ValueError("temporal_window_hours must be greater than zero.")
    if config.minimum_lexical_tokens < 1:
        raise ValueError("minimum_lexical_tokens must be greater than zero.")
    weights = (
        config.temporal_weight,
        config.entity_weight,
        config.lexical_weight,
    )
    if any(weight < 0 for weight in weights):
        raise ValueError("Event similarity weights cannot be negative.")
    if sum(weights) <= 0:
        raise ValueError("At least one event similarity weight is required.")


def _validate_pair(
        left: DocumentAnalysisInputBundle,
        right: DocumentAnalysisInputBundle,
) -> None:
    if (
        left.document.document_version_id
        == right.document.document_version_id
    ):
        raise ValueError("Two distinct document versions are required.")
    if left.document.document_id == right.document.document_id:
        raise ValueError("Two distinct documents are required.")
    for side, bundle in (("left", left), ("right", right)):
        readiness = assess_event_text_readiness(
            identifier_scheme=bundle.document.identifier_scheme,
            identifier_value=bundle.document.identifier_value,
            artifact_type=bundle.text.artifact_type,
            text=bundle.text.text,
        )
        if not readiness.ready_for_event_analysis:
            raise ValueError(
                f"{side.capitalize()} event-analysis text is blocked: "
                + " ".join(readiness.reasons)
            )


def _temporal_signal(
        left: DocumentAnalysisInputBundle,
        right: DocumentAnalysisInputBundle,
        config: EventSimilarityConfiguration,
) -> EventSimilaritySignal:
    left_time = left.document.published_at
    right_time = right.document.published_at
    if left_time is None or right_time is None:
        return EventSimilaritySignal(
            name="temporal",
            available=False,
            score=None,
            configured_weight=config.temporal_weight,
            effective_weight=0.0,
            contribution=None,
            explanation="Both publication timestamps are required.",
        )
    delta_hours = abs(
        (_as_utc(left_time) - _as_utc(right_time)).total_seconds()
    ) / 3600.0
    score = max(
        0.0,
        1.0 - (delta_hours / config.temporal_window_hours),
    )
    return EventSimilaritySignal(
        name="temporal",
        available=True,
        score=round(score, 12),
        configured_weight=config.temporal_weight,
        effective_weight=0.0,
        contribution=None,
        explanation=(
            f"publication_delta_hours={delta_hours:.6f}; "
            "linear_decay_window_hours="
            f"{config.temporal_window_hours:.6f}"
        ),
    )


def _entity_signal(
        left: DocumentAnalysisInputBundle,
        right: DocumentAnalysisInputBundle,
        config: EventSimilarityConfiguration,
) -> tuple[EventSimilaritySignal, tuple[int, ...]]:
    left_ids = {item.entity_id for item in left.entities.items}
    right_ids = {item.entity_id for item in right.entities.items}
    shared = tuple(sorted(left_ids & right_ids))
    union = left_ids | right_ids
    if not union:
        return (
            EventSimilaritySignal(
                name="entities",
                available=False,
                score=None,
                configured_weight=config.entity_weight,
                effective_weight=0.0,
                contribution=None,
                explanation="Neither document has a resolved entity.",
            ),
            shared,
        )
    score = len(shared) / len(union)
    return (
        EventSimilaritySignal(
            name="entities",
            available=True,
            score=round(score, 12),
            configured_weight=config.entity_weight,
            effective_weight=0.0,
            contribution=None,
            explanation=(
                f"shared={len(shared)} union={len(union)}; "
                "set_similarity=jaccard"
            ),
        ),
        shared,
    )


def _lexical_signal(
        left: DocumentAnalysisInputBundle,
        right: DocumentAnalysisInputBundle,
        config: EventSimilarityConfiguration,
) -> EventSimilaritySignal:
    left_terms = _term_counts(left.text.text)
    right_terms = _term_counts(right.text.text)
    left_count = sum(left_terms.values())
    right_count = sum(right_terms.values())
    if (
        left_count < config.minimum_lexical_tokens
        or right_count < config.minimum_lexical_tokens
    ):
        return EventSimilaritySignal(
            name="lexical",
            available=False,
            score=None,
            configured_weight=config.lexical_weight,
            effective_weight=0.0,
            contribution=None,
            explanation=(
                f"token_counts={left_count},{right_count}; required_each="
                f"{config.minimum_lexical_tokens}"
            ),
        )
    score = _cosine_similarity(left_terms, right_terms)
    return EventSimilaritySignal(
        name="lexical",
        available=True,
        score=round(score, 12),
        configured_weight=config.lexical_weight,
        effective_weight=0.0,
        contribution=None,
        explanation=(
            f"token_counts={left_count},{right_count}; "
            "similarity=term_frequency_cosine"
        ),
    )


def _with_contribution(
        signal: EventSimilaritySignal,
        *,
        available_weight: float,
) -> EventSimilaritySignal:
    if not signal.available or signal.score is None:
        return signal
    if available_weight <= 0.0:
        return EventSimilaritySignal(
            name=signal.name,
            available=True,
            score=signal.score,
            configured_weight=signal.configured_weight,
            effective_weight=0.0,
            contribution=0.0,
            explanation=signal.explanation,
        )
    effective_weight = signal.configured_weight / available_weight
    return EventSimilaritySignal(
        name=signal.name,
        available=True,
        score=signal.score,
        configured_weight=signal.configured_weight,
        effective_weight=round(effective_weight, 12),
        contribution=round(signal.score * effective_weight, 12),
        explanation=signal.explanation,
    )


def _term_counts(text: str) -> Counter[str]:
    return Counter(
        token
        for match in _WORD_PATTERN.finditer(text.casefold())
        if len(token := match.group(0)) >= 3 and not token.isdigit()
    )


def _cosine_similarity(
        left: Counter[str],
        right: Counter[str],
) -> float:
    shared = left.keys() & right.keys()
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = sqrt(sum(value * value for value in left.values()))
    right_norm = sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
