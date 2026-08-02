import unittest

from argus.analysis.methods import (
    AnalysisMethodRegistry,
    LEXICAL_DISCOURSE_METHOD_VERSION,
    LexicalDiscourseMethod,
    SYNTHETIC_ORIGIN_TEXT_METHOD_VERSION,
    SyntheticOriginTextMethod,
    default_analysis_method_registry,
)
from argus.analysis.schemas import (
    DiscourseMetrics,
    EvidenceCategory,
    EvidenceSpan,
)
from argus.analysis.synthetic_origin import SyntheticOriginConclusion


class StubAnalyzer:
    def analyze(self, text: str) -> DiscourseMetrics:
        self.text = text
        return DiscourseMetrics(
            word_count=4,
            sentence_count=1,
            average_sentence_length=4.0,
            question_count=0,
            exclamation_count=1,
            first_person_plural_count=1,
            third_person_plural_count=0,
            certainty_marker_count=1,
            uncertainty_marker_count=0,
            fear_marker_count=0,
            threat_marker_count=0,
            evidence=[
                EvidenceSpan(
                    category=EvidenceCategory.CERTAINTY,
                    sentence="We clearly agree!",
                    start_char=0,
                    end_char=17,
                    matched_terms=["clearly"],
                )
            ],
        )


class AnalysisMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = StubAnalyzer()
        self.method = LexicalDiscourseMethod(self.analyzer)
        self.manifest = {
            "document": {"language": "en"},
            "text": {"derived_artifact_id": 12},
        }

    def test_lexical_method_returns_json_metrics_and_evidence(self) -> None:
        output = self.method.execute(
            text="We clearly agree!",
            input_manifest=self.manifest,
            configuration={},
        )

        self.assertEqual(output.result_schema_version,
                         "lexical-discourse-result@2")
        self.assertEqual(output.payload["metrics"]["word_count"], 4)
        self.assertNotIn("evidence", output.payload)
        self.assertEqual(
            output.evidence[0].payload,
            {"excerpt": "We clearly agree!", "matched_terms": ["clearly"]},
        )
        self.assertEqual(output.evidence[0].locator["start_char"], 0)
        self.assertEqual(output.evidence[0].locator["end_char"], 17)
        self.assertEqual(self.analyzer.text, "We clearly agree!")

    def test_lexical_method_rejects_language_and_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "English"):
            self.method.execute(
                text="Текст.",
                input_manifest={
                    "document": {"language": "ru"},
                    "text": {"derived_artifact_id": 12},
                },
                configuration={},
            )
        with self.assertRaisesRegex(ValueError, "does not accept"):
            self.method.execute(
                text="Text.",
                input_manifest=self.manifest,
                configuration={"threshold": 0.5},
            )

    def test_registry_requires_exact_name_and_version(self) -> None:
        registry = AnalysisMethodRegistry((self.method,))
        self.assertIs(
            registry.require(
                "lexical-discourse",
                LEXICAL_DISCOURSE_METHOD_VERSION,
            ),
            self.method,
        )
        with self.assertRaisesRegex(ValueError, "Supported"):
            registry.require("lexical-discourse", "unknown")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            AnalysisMethodRegistry((self.method, self.method))

    def test_synthetic_origin_short_text_is_explicitly_unscored(self) -> None:
        method = SyntheticOriginTextMethod()
        text = "It is important to note that this text is short."

        output = method.execute(
            text=text,
            input_manifest=self.manifest,
            configuration={},
        )

        self.assertEqual(
            output.result_schema_version,
            "synthetic-origin-text-result@1",
        )
        self.assertEqual(output.payload["conclusion"], "inconclusive")
        self.assertFalse(output.payload["eligible_for_scoring"])
        self.assertIsNone(output.payload["detector_score"])
        self.assertIsNone(output.payload["synthetic_probability"])
        self.assertFalse(output.payload["probability_is_calibrated"])
        self.assertEqual(len(output.warnings), 1)
        self.assertEqual(len(output.evidence), 1)
        evidence = output.evidence[0]
        self.assertEqual(evidence.category, "formulaic_phrase")
        self.assertEqual(
            text[
                evidence.locator["start_char"]:
                evidence.locator["end_char"]
            ],
            evidence.payload["excerpt"],
        )

    def test_synthetic_origin_long_text_gets_only_detector_score(self) -> None:
        method = SyntheticOriginTextMethod()
        sentence = (
            "Moreover, the system records every observation and preserves "
            "the supporting source for later review."
        )
        text = " ".join(sentence for _ in range(30))

        output = method.execute(
            text=text,
            input_manifest=self.manifest,
            configuration={},
        )

        self.assertTrue(output.payload["eligible_for_scoring"])
        self.assertIsInstance(output.payload["detector_score"], float)
        self.assertGreaterEqual(output.payload["detector_score"], 0.0)
        self.assertLessEqual(output.payload["detector_score"], 1.0)
        self.assertIsNone(output.payload["synthetic_probability"])
        self.assertEqual(output.payload["conclusion"], "inconclusive")
        self.assertEqual(output.warnings, ())
        self.assertEqual(len(output.evidence), 30)

    def test_synthetic_origin_rejects_language_and_configuration(self) -> None:
        method = SyntheticOriginTextMethod()
        with self.assertRaisesRegex(ValueError, "English"):
            method.validate(
                input_manifest={
                    "document": {"language": "ru"},
                    "text": {"derived_artifact_id": 12},
                },
                configuration={},
            )
        with self.assertRaisesRegex(ValueError, "does not accept"):
            method.validate(
                input_manifest=self.manifest,
                configuration={"threshold": 0.5},
            )

    def test_default_registry_exposes_exact_synthetic_origin_version(self) -> None:
        method = default_analysis_method_registry().require(
            "synthetic-origin-text",
            SYNTHETIC_ORIGIN_TEXT_METHOD_VERSION,
        )
        self.assertIsInstance(method, SyntheticOriginTextMethod)

    def test_synthetic_origin_conclusions_exclude_verified_human(self) -> None:
        conclusions = {item.value for item in SyntheticOriginConclusion}
        self.assertNotIn("verified_human", conclusions)
        self.assertEqual(conclusions, {
            "verified_synthetic",
            "verified_ai_edited",
            "synthetic_signals_detected",
            "no_synthetic_signals_detected",
            "inconclusive",
            "provenance_invalid",
        })
