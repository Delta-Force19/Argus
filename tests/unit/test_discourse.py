import pytest
import spacy
from argus.analysis.schemas import EvidenceCategory

from argus.analysis.discourse_analyzer import (
    DiscourseAnalyzer,
)


@pytest.fixture(scope="module")
def analyzer() -> DiscourseAnalyzer:
    return DiscourseAnalyzer()


def test_basic_metrics(
        analyzer: DiscourseAnalyzer,
) -> None:
    text = (
        "We must protect our country. "
        "They may attack us!"
    )

    metrics = analyzer.analyze(text)

    assert metrics.word_count == 9
    assert metrics.sentence_count == 2
    assert metrics.average_sentence_length == 4.5

    assert metrics.question_count == 0
    assert metrics.exclamation_count == 1

    assert metrics.first_person_plural_count == 3
    assert metrics.third_person_plural_count == 1

def test_lexicon_metrics_include_evidence(
            analyzer: DiscourseAnalyzer,
) -> None:
    text = (
        "We must protect our country. "
        "They may attack us!"
    )

    metrics = analyzer.analyze(text)

    assert metrics.certainty_marker_count == 1
    assert metrics.uncertainty_marker_count == 1
    assert metrics.fear_marker_count == 0
    assert metrics.threat_marker_count == 1

    evidence_by_category = {
        evidence.category: evidence
        for evidence in metrics.evidence
    }

    assert evidence_by_category[EvidenceCategory.CERTAINTY].matched_terms == ["must"]

    assert evidence_by_category[EvidenceCategory.UNCERTAINTY].matched_terms == ["may"]

    assert evidence_by_category[EvidenceCategory.THREAT].matched_terms == ["attack"]


def test_repeated_category_markers_are_counted(
        analyzer: DiscourseAnalyzer,
) -> None:
    metrics = analyzer.analyze(
        "We will certainly prevail."
    )

    assert metrics.certainty_marker_count == 2

    assert metrics.evidence[0].matched_terms == [
        "certainly",
        "will",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "\n\t",
    ],
)
def test_empty_text_is_rejected(
        analyzer: DiscourseAnalyzer,
        text: str,
) -> None:
    with pytest.raises(
            ValueError,
            match="Text must not be empty",
    ):
        analyzer.analyze(text)

def test_custom_language_pipeline_can_be_injected():
    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")

    analyzer = DiscourseAnalyzer(nlp=nlp)
    metrics = analyzer.analyze("Simple test.")

    assert metrics.word_count == 2
    assert metrics.sentence_count == 1