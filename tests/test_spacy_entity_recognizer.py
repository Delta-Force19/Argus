import unittest

import spacy

from argus.knowledge import EntityType
from argus.recognizers.spacy_entity_recognizer import (
    SpacyEntityRecognizer,
)


def entity_pipeline(
        language: str,
        *,
        version: str,
        patterns: list[dict[str, object]],
):
    pipeline = spacy.blank(language)
    pipeline.meta["version"] = version
    ruler = pipeline.add_pipe("entity_ruler")
    ruler.add_patterns(patterns)
    return pipeline


class SpacyEntityRecognizerTests(unittest.TestCase):
    def test_recognizes_english_mentions_with_exact_offsets(self) -> None:
        pipeline = entity_pipeline(
            "en",
            version="test-en",
            patterns=[
                {"label": "PERSON", "pattern": "Ada Lovelace"},
                {"label": "ORG", "pattern": "Royal Society"},
            ],
        )
        recognizer = SpacyEntityRecognizer(
            pipelines={"en": pipeline}
        )
        text = "Ada Lovelace addressed the Royal Society."

        result = recognizer.recognize(text, language="en-US")

        self.assertEqual(
            [
                (
                    mention.entity_type,
                    mention.surface_text,
                    mention.normalized_text,
                    mention.start_char,
                    mention.end_char,
                )
                for mention in result.mentions
            ],
            [
                (EntityType.PERSON, "Ada Lovelace", "ada lovelace", 0, 12),
                (
                    EntityType.ORGANIZATION,
                    "Royal Society",
                    "royal society",
                    27,
                    40,
                ),
            ],
        )
        self.assertEqual(
            recognizer.method_version("en"),
            "en_core_web_sm@test-en",
        )

    def test_normalizes_russian_model_labels(self) -> None:
        pipeline = entity_pipeline(
            "ru",
            version="test-ru",
            patterns=[
                {"label": "PER", "pattern": "Анна Каренина"},
                {"label": "ORG", "pattern": "МГУ"},
            ],
        )
        recognizer = SpacyEntityRecognizer(
            pipelines={"ru": pipeline}
        )

        result = recognizer.recognize(
            "Анна Каренина училась в МГУ.",
            language="ru",
        )

        self.assertEqual(
            [mention.entity_type for mention in result.mentions],
            [EntityType.PERSON, EntityType.ORGANIZATION],
        )
        self.assertEqual(
            [mention.normalized_text for mention in result.mentions],
            ["анна каренина", "мгу"],
        )
        self.assertEqual(
            recognizer.method_version("ru-RU"),
            "ru_core_news_sm@test-ru",
        )

    def test_preserves_unknown_source_label_as_other(self) -> None:
        pipeline = entity_pipeline(
            "en",
            version="test",
            patterns=[{"label": "CUSTOM", "pattern": "Argus"}],
        )
        recognizer = SpacyEntityRecognizer(
            pipelines={"en": pipeline}
        )

        mention = recognizer.recognize(
            "Argus",
            language="en",
        ).mentions[0]

        self.assertEqual(mention.entity_type, EntityType.OTHER)
        self.assertEqual(mention.source_label, "CUSTOM")

    def test_rejects_unsupported_language(self) -> None:
        recognizer = SpacyEntityRecognizer(pipelines={})

        with self.assertRaisesRegex(ValueError, "Unsupported"):
            recognizer.recognize("Texte", language="fr")


if __name__ == "__main__":
    unittest.main()
