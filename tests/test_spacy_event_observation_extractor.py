import unittest

import spacy
from spacy.tokens import Doc, Span

from argus.analysis.spacy_event_observation_extractor import (
    SpacyEventObservationExtractor,
)
from argus.event_observations import EventObservationType


class SpacyEventObservationExtractorTests(unittest.TestCase):
    def test_extracts_entity_action_and_object_as_candidates(self) -> None:
        vocabulary = spacy.blank("en").vocab
        document = Doc(
            vocabulary,
            words=["Cyprus", "contained", "fires", "."],
            spaces=[True, True, False, False],
            heads=[1, 1, 1, 1],
            deps=["nsubj", "ROOT", "obj", "punct"],
            pos=["PROPN", "VERB", "NOUN", "PUNCT"],
            lemmas=["Cyprus", "contain", "fire", "."],
        )
        document.ents = (Span(document, 0, 1, label="GPE"),)
        extractor = SpacyEventObservationExtractor(
            pipelines={"en": _StaticPipeline(document)}
        )

        result = extractor.extract("Cyprus contained fires.", language="en")

        self.assertEqual(
            tuple(item.observation_type for item in result.observations),
            (
                EventObservationType.PLACE_MENTION,
                EventObservationType.ACTION_CANDIDATE,
                EventObservationType.OBJECT_CANDIDATE,
            ),
        )
        self.assertEqual(result.observations[0].surface_text, "Cyprus")
        self.assertEqual(result.observations[1].normalized_value, "contain")
        self.assertEqual(result.observations[2].surface_text, "fires")
        self.assertTrue(result.quality_limitations)
        self.assertEqual(
            extractor.method_version("en-US"),
            "en_core_web_sm@test-1",
        )

    def test_rejects_unsupported_language(self) -> None:
        extractor = SpacyEventObservationExtractor(pipelines={})

        with self.assertRaisesRegex(ValueError, "supported languages"):
            extractor.extract("Texte.", language="fr")


class _StaticPipeline:
    meta = {"version": "test-1"}

    def __init__(self, document: Doc) -> None:
        self.document = document

    def __call__(self, text: str) -> Doc:
        if text != self.document.text:
            raise AssertionError("Unexpected extractor input.")
        return self.document


if __name__ == "__main__":
    unittest.main()
