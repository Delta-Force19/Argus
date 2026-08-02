import unittest

from argus.analysis.methods import (
    AnalysisMethodRegistry,
    LEXICAL_DISCOURSE_METHOD_VERSION,
    LexicalDiscourseMethod,
)
from argus.analysis.schemas import (
    DiscourseMetrics,
    EvidenceCategory,
    EvidenceSpan,
)


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
