from collections.abc import Iterable

import spacy
from spacy.language import Language
from spacy.tokens import Span, Token

from argus.analysis.lexicons import (
    LEXICONS_BY_CATEGORY,
)
from argus.analysis.schemas import (
    DiscourseMetrics,
    EvidenceCategory,
    EvidenceSpan,
)


class DiscourseAnalyzer:
    def __init__(
            self,
            model_name: str = "en_core_web_sm",
            *,
            nlp: Language | None = None,
    ) -> None:
        self.nlp = (
            nlp
            if nlp is not None
            else spacy.load(model_name)
        )

    def analyze(
            self,
            text: str,
    ) -> DiscourseMetrics:
        if not text or not text.strip():
            raise ValueError(
                "Text must not be empty."
            )

        doc = self.nlp(text)

        sentences = list(doc.sents)
        word_tokens = [
            token
            for token in doc
            if not token.is_space
               and not token.is_punct
        ]

        evidence: list[EvidenceSpan] = []
        marker_counts: dict[
            EvidenceCategory,
            int,
        ] = {}

        for category, lexicon in LEXICONS_BY_CATEGORY:
            count, category_evidence = (
                self._find_lexicon_matches(
                    sentences=sentences,
                    lexicon=lexicon,
                    category=category,
                )
            )

            marker_counts[category] = count
            evidence.extend(category_evidence)
        first_person_plural_count = (
            self._count_lemmas(
                word_tokens,
                {"we", "our", "ours", "us"},
            )
        )
        third_person_plural_count = (
            self._count_lemmas(
                word_tokens,
                {"they", "their", "theirs", "them"},
            )
        )

        sentence_count = len(sentences)
        word_count = len(word_tokens)

        average_sentence_length = (
            word_count / sentence_count
            if sentence_count > 0
            else 0.0
        )

        return DiscourseMetrics(
            word_count=word_count,
            sentence_count=sentence_count,
            average_sentence_length=round(
                average_sentence_length,
                2,
            ),
            question_count=text.count("?"),
            exclamation_count=text.count("!"),
            first_person_plural_count=(
                first_person_plural_count
            ),
            third_person_plural_count=(
                third_person_plural_count
            ),
            certainty_marker_count=marker_counts[
                EvidenceCategory.CERTAINTY
            ],
            uncertainty_marker_count=marker_counts[
                EvidenceCategory.UNCERTAINTY
            ],
            fear_marker_count=marker_counts[
                EvidenceCategory.FEAR
            ],
            threat_marker_count=marker_counts[
                EvidenceCategory.THREAT
            ],
            evidence=evidence,
        )

    @staticmethod
    def _count_lemmas(
            tokens: Iterable[Token],
            lemmas: set[str],
    ) -> int:
        return sum(
            1
            for token in tokens
            if token.lemma_.lower() in lemmas
        )

    @staticmethod
    def _find_lexicon_matches(
            sentences: list[Span],
            lexicon: frozenset[str],
            category: EvidenceCategory,
    ) -> tuple[int, list[EvidenceSpan]]:
        total = 0
        evidence: list[EvidenceSpan] = []

        for sentence in sentences:
            matched_terms: list[str] = []

            for token in sentence:
                lemma = token.lemma_.lower()

                if lemma in lexicon:
                    matched_terms.append(lemma)

            if not matched_terms:
                continue

            total += len(matched_terms)

            evidence.append(
                EvidenceSpan(
                    category=category,
                    sentence=sentence.text.strip(),
                    matched_terms=sorted(
                        set(matched_terms)
                    ),
                )
            )

        return total, evidence