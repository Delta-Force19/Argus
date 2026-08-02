from dataclasses import dataclass
from enum import Enum
import re


MINIMUM_WORD_COUNT = 250
MINIMUM_SENTENCE_COUNT = 10


class SyntheticOriginConclusion(str, Enum):
    """Allowed conclusions for synthetic-origin analysis."""

    VERIFIED_SYNTHETIC = "verified_synthetic"
    VERIFIED_AI_EDITED = "verified_ai_edited"
    SYNTHETIC_SIGNALS_DETECTED = "synthetic_signals_detected"
    NO_SYNTHETIC_SIGNALS_DETECTED = "no_synthetic_signals_detected"
    INCONCLUSIVE = "inconclusive"
    PROVENANCE_INVALID = "provenance_invalid"


@dataclass(frozen=True, slots=True)
class SyntheticTextSignal:
    """One local structural signal; it is not proof of AI authorship."""

    category: str
    phrase: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class SyntheticTextAssessment:
    """Uncalibrated structural assessment for exploratory ranking."""

    conclusion: SyntheticOriginConclusion
    eligible_for_scoring: bool
    detector_score: float | None
    word_count: int
    sentence_count: int
    average_sentence_length: float
    sentence_length_variation: float
    lexical_diversity: float
    formulaic_phrase_count: int
    formulaic_phrase_density: float
    signals: tuple[SyntheticTextSignal, ...]
    limitations: tuple[str, ...]


class StructuralSyntheticTextAnalyzer:
    """Extract reproducible text-origin signals without claiming probability."""

    _WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)?")
    _SENTENCE_RE = re.compile(r"\S(?:.*?\S)?(?:[.!?]+(?=\s|$)|$)", re.DOTALL)
    _FORMULAIC_PHRASES = (
        "in conclusion",
        "it is important to note",
        "it is worth noting",
        "delve into",
        "a testament to",
        "in today's rapidly evolving",
        "in the ever-evolving",
        "plays a crucial role",
        "shed light on",
        "multifaceted",
        "tapestry of",
        "moreover",
        "furthermore",
    )

    def analyze(self, text: str) -> SyntheticTextAssessment:
        words = self._WORD_RE.findall(text)
        sentences = [
            self._WORD_RE.findall(match.group(0))
            for match in self._SENTENCE_RE.finditer(text)
            if self._WORD_RE.search(match.group(0))
        ]
        sentence_lengths = [len(sentence) for sentence in sentences]
        word_count = len(words)
        sentence_count = len(sentences)
        average = (
            sum(sentence_lengths) / sentence_count
            if sentence_count else 0.0
        )
        variation = _coefficient_of_variation(sentence_lengths, average)
        lexical_diversity = (
            len({word.lower() for word in words}) / word_count
            if word_count else 0.0
        )
        signals = self._formulaic_signals(text)
        density = (
            len(signals) * 1000 / word_count
            if word_count else 0.0
        )
        eligible = (
            word_count >= MINIMUM_WORD_COUNT
            and sentence_count >= MINIMUM_SENTENCE_COUNT
        )
        score = (
            _structural_score(
                sentence_length_variation=variation,
                lexical_diversity=lexical_diversity,
                formulaic_phrase_density=density,
            )
            if eligible else None
        )
        limitations = [
            "This structural heuristic is not calibrated as a probability.",
            "Human editing, genre and translation can change every signal.",
            "A score cannot establish either AI or human authorship.",
        ]
        if not eligible:
            limitations.append(
                "At least 250 words and 10 sentences are required for a score."
            )
        return SyntheticTextAssessment(
            conclusion=SyntheticOriginConclusion.INCONCLUSIVE,
            eligible_for_scoring=eligible,
            detector_score=score,
            word_count=word_count,
            sentence_count=sentence_count,
            average_sentence_length=round(average, 2),
            sentence_length_variation=round(variation, 4),
            lexical_diversity=round(lexical_diversity, 4),
            formulaic_phrase_count=len(signals),
            formulaic_phrase_density=round(density, 4),
            signals=signals,
            limitations=tuple(limitations),
        )

    def _formulaic_signals(self, text: str) -> tuple[SyntheticTextSignal, ...]:
        signals: list[SyntheticTextSignal] = []
        for phrase in self._FORMULAIC_PHRASES:
            pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                signals.append(SyntheticTextSignal(
                    category="formulaic_phrase",
                    phrase=match.group(0),
                    start_char=match.start(),
                    end_char=match.end(),
                ))
        return tuple(sorted(signals, key=lambda item: (
            item.start_char,
            item.end_char,
            item.phrase.lower(),
        )))


def _coefficient_of_variation(
        values: list[int],
        average: float,
) -> float:
    if len(values) < 2 or average == 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return variance ** 0.5 / average


def _structural_score(
        *,
        sentence_length_variation: float,
        lexical_diversity: float,
        formulaic_phrase_density: float,
) -> float:
    uniformity = 1.0 - min(sentence_length_variation, 1.0)
    diversity_signal = 1.0 - min(max(lexical_diversity, 0.0), 1.0)
    formulaic_signal = min(formulaic_phrase_density / 8.0, 1.0)
    score = (
        0.35 * uniformity
        + 0.25 * diversity_signal
        + 0.40 * formulaic_signal
    )
    return round(min(max(score, 0.0), 1.0), 4)
