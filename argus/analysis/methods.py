from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from argus.analysis.discourse_analyzer import DiscourseAnalyzer
from argus.analysis.synthetic_origin import StructuralSyntheticTextAnalyzer


LEXICAL_DISCOURSE_METHOD = "lexical-discourse"
LEXICAL_DISCOURSE_METHOD_VERSION = "lexical-en-v0.2"
LEXICAL_DISCOURSE_RESULT_SCHEMA = "lexical-discourse-result@2"
LEXICAL_DISCOURSE_EVIDENCE_SCHEMA = "lexical-discourse-evidence@1"
SYNTHETIC_ORIGIN_TEXT_METHOD = "synthetic-origin-text"
SYNTHETIC_ORIGIN_TEXT_METHOD_VERSION = "structural-en-v0.1"
SYNTHETIC_ORIGIN_TEXT_RESULT_SCHEMA = "synthetic-origin-text-result@1"
SYNTHETIC_ORIGIN_TEXT_EVIDENCE_SCHEMA = "synthetic-origin-text-evidence@1"


@dataclass(frozen=True, slots=True)
class AnalysisMethodEvidence:
    """One JSON-compatible, source-located analytical observation."""

    evidence_schema_version: str
    category: str
    modality: str
    locator: Mapping[str, object]
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AnalysisMethodOutput:
    """JSON-compatible output returned by one registered method."""

    result_schema_version: str
    payload: Mapping[str, object]
    warnings: tuple[str, ...] = ()
    evidence: tuple[AnalysisMethodEvidence, ...] = ()


class AnalysisMethod(Protocol):
    name: str
    version: str

    def validate(
            self,
            *,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> None:
        """Reject an input or configuration this version cannot execute."""

    def execute(
            self,
            *,
            text: str,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> AnalysisMethodOutput:
        """Execute one exact method version against persisted input."""


class LexicalDiscourseMethod:
    """Adapt the existing deterministic English analyzer to AnalysisRun."""

    name = LEXICAL_DISCOURSE_METHOD
    version = LEXICAL_DISCOURSE_METHOD_VERSION

    def __init__(self, analyzer: DiscourseAnalyzer | None = None) -> None:
        self._analyzer = analyzer

    def validate(
            self,
            *,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> None:
        if configuration:
            raise ValueError(
                "lexical-discourse does not accept configuration values."
            )
        document = input_manifest.get("document")
        if not isinstance(document, dict):
            raise ValueError("Analysis input document manifest is invalid.")
        language = document.get("language")
        if not isinstance(language, str) or not (
            language.lower() == "en"
            or language.lower().startswith("en-")
        ):
            raise ValueError(
                "lexical-discourse requires an English document."
            )

    def execute(
            self,
            *,
            text: str,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> AnalysisMethodOutput:
        self.validate(
            input_manifest=input_manifest,
            configuration=configuration,
        )

        analyzer = self._analyzer or DiscourseAnalyzer()
        metrics = analyzer.analyze(text)
        text_manifest = input_manifest.get("text")
        if not isinstance(text_manifest, dict):
            raise ValueError("Analysis input text manifest is invalid.")
        artifact_id = text_manifest.get("derived_artifact_id")
        if not isinstance(artifact_id, int) or isinstance(artifact_id, bool):
            raise ValueError("Analysis input text artifact id is invalid.")
        payload: dict[str, object] = {
            "metrics": {
                "word_count": metrics.word_count,
                "sentence_count": metrics.sentence_count,
                "average_sentence_length": (
                    metrics.average_sentence_length
                ),
                "question_count": metrics.question_count,
                "exclamation_count": metrics.exclamation_count,
                "first_person_plural_count": (
                    metrics.first_person_plural_count
                ),
                "third_person_plural_count": (
                    metrics.third_person_plural_count
                ),
                "certainty_marker_count": (
                    metrics.certainty_marker_count
                ),
                "uncertainty_marker_count": (
                    metrics.uncertainty_marker_count
                ),
                "fear_marker_count": metrics.fear_marker_count,
                "threat_marker_count": metrics.threat_marker_count,
            },
        }
        return AnalysisMethodOutput(
            result_schema_version=LEXICAL_DISCOURSE_RESULT_SCHEMA,
            payload=payload,
            evidence=tuple(
                AnalysisMethodEvidence(
                    evidence_schema_version=(
                        LEXICAL_DISCOURSE_EVIDENCE_SCHEMA
                    ),
                    category=item.category.value,
                    modality="text",
                    locator={
                        "type": "text_span",
                        "derived_artifact_id": artifact_id,
                        "start_char": item.start_char,
                        "end_char": item.end_char,
                        "content_sha256": sha256(
                            item.sentence.encode("utf-8")
                        ).hexdigest(),
                    },
                    payload={
                        "excerpt": item.sentence,
                        "matched_terms": list(item.matched_terms),
                    },
                )
                for item in metrics.evidence
            ),
        )


class SyntheticOriginTextMethod:
    """Expose an explicitly uncalibrated structural text assessment."""

    name = SYNTHETIC_ORIGIN_TEXT_METHOD
    version = SYNTHETIC_ORIGIN_TEXT_METHOD_VERSION

    def __init__(
            self,
            analyzer: StructuralSyntheticTextAnalyzer | None = None,
    ) -> None:
        self._analyzer = analyzer

    def validate(
            self,
            *,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> None:
        if configuration:
            raise ValueError(
                "synthetic-origin-text does not accept configuration values."
            )
        document = input_manifest.get("document")
        if not isinstance(document, dict):
            raise ValueError("Analysis input document manifest is invalid.")
        language = document.get("language")
        if not isinstance(language, str) or not (
            language.lower() == "en"
            or language.lower().startswith("en-")
        ):
            raise ValueError(
                "synthetic-origin-text requires an English document."
            )

    def execute(
            self,
            *,
            text: str,
            input_manifest: Mapping[str, object],
            configuration: Mapping[str, object],
    ) -> AnalysisMethodOutput:
        self.validate(
            input_manifest=input_manifest,
            configuration=configuration,
        )
        text_manifest = input_manifest.get("text")
        if not isinstance(text_manifest, dict):
            raise ValueError("Analysis input text manifest is invalid.")
        artifact_id = text_manifest.get("derived_artifact_id")
        if not isinstance(artifact_id, int) or isinstance(artifact_id, bool):
            raise ValueError("Analysis input text artifact id is invalid.")
        assessment = (
            self._analyzer or StructuralSyntheticTextAnalyzer()
        ).analyze(text)
        payload: dict[str, object] = {
            "modality": "text",
            "assessment_mode": "passive_structural_heuristic",
            "experimental": True,
            "conclusion": assessment.conclusion.value,
            "scope": "whole_document",
            "eligible_for_scoring": assessment.eligible_for_scoring,
            "detector_score": assessment.detector_score,
            "synthetic_probability": None,
            "probability_is_calibrated": False,
            "metrics": {
                "word_count": assessment.word_count,
                "sentence_count": assessment.sentence_count,
                "average_sentence_length": (
                    assessment.average_sentence_length
                ),
                "sentence_length_variation": (
                    assessment.sentence_length_variation
                ),
                "lexical_diversity": assessment.lexical_diversity,
                "formulaic_phrase_count": (
                    assessment.formulaic_phrase_count
                ),
                "formulaic_phrase_density_per_1000_words": (
                    assessment.formulaic_phrase_density
                ),
            },
            "limitations": list(assessment.limitations),
        }
        warnings = (
            (
                "Input is too short for the uncalibrated structural score; "
                "the result remains inconclusive."
            ),
        ) if not assessment.eligible_for_scoring else ()
        return AnalysisMethodOutput(
            result_schema_version=SYNTHETIC_ORIGIN_TEXT_RESULT_SCHEMA,
            payload=payload,
            warnings=warnings,
            evidence=tuple(
                AnalysisMethodEvidence(
                    evidence_schema_version=(
                        SYNTHETIC_ORIGIN_TEXT_EVIDENCE_SCHEMA
                    ),
                    category=signal.category,
                    modality="text",
                    locator={
                        "type": "text_span",
                        "derived_artifact_id": artifact_id,
                        "start_char": signal.start_char,
                        "end_char": signal.end_char,
                        "content_sha256": sha256(
                            signal.phrase.encode("utf-8")
                        ).hexdigest(),
                    },
                    payload={
                        "excerpt": signal.phrase,
                        "signal": "formulaic_language",
                        "interpretation": (
                            "Weak structural signal; not proof of synthetic "
                            "authorship."
                        ),
                    },
                )
                for signal in assessment.signals
            ),
        )

class AnalysisMethodRegistry:
    """Resolve only explicit, exact analytical method versions."""

    def __init__(self, methods: tuple[AnalysisMethod, ...] = ()) -> None:
        self._methods: dict[tuple[str, str], AnalysisMethod] = {}
        for method in methods:
            key = (method.name, method.version)
            if key in self._methods:
                raise ValueError(
                    "Duplicate analysis method registration: "
                    f"{method.name}@{method.version}."
                )
            self._methods[key] = method

    def require(self, name: str, version: str) -> AnalysisMethod:
        method = self._methods.get((name, version))
        if method is None:
            supported = ", ".join(
                f"{item_name}@{item_version}"
                for item_name, item_version in sorted(self._methods)
            ) or "none"
            raise ValueError(
                "Analysis method is not registered: "
                f"{name}@{version}. Supported: {supported}."
            )
        return method


def default_analysis_method_registry() -> AnalysisMethodRegistry:
    return AnalysisMethodRegistry((
        LexicalDiscourseMethod(),
        SyntheticOriginTextMethod(),
    ))
