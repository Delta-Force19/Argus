import re
import unicodedata
from collections.abc import Mapping

import spacy
from spacy.language import Language

from argus.knowledge import (
    EntityRecognitionResult,
    EntityType,
    RecognizedEntityMention,
)


class SpacyEntityRecognizer:
    """Recognize English and Russian entity mentions with spaCy."""

    MODEL_NAMES = {
        "en": "en_core_web_sm",
        "ru": "ru_core_news_sm",
    }
    LABEL_TYPES = {
        "PERSON": EntityType.PERSON,
        "PER": EntityType.PERSON,
        "ORG": EntityType.ORGANIZATION,
        "GPE": EntityType.LOCATION,
        "LOC": EntityType.LOCATION,
        "NORP": EntityType.GROUP,
        "FAC": EntityType.FACILITY,
        "PRODUCT": EntityType.PRODUCT,
        "EVENT": EntityType.EVENT,
        "WORK_OF_ART": EntityType.WORK,
        "LAW": EntityType.LAW,
        "LANGUAGE": EntityType.LANGUAGE,
        "DATE": EntityType.DATE,
        "TIME": EntityType.TIME,
        "PERCENT": EntityType.PERCENT,
        "MONEY": EntityType.MONEY,
        "QUANTITY": EntityType.QUANTITY,
        "ORDINAL": EntityType.ORDINAL,
        "CARDINAL": EntityType.CARDINAL,
    }
    QUALITY_LIMITATIONS = (
        "Statistical named-entity recognition can omit or misclassify mentions.",
        "Normalized mention text does not establish entity identity.",
    )

    def __init__(
            self,
            *,
            pipelines: Mapping[str, Language] | None = None,
    ) -> None:
        self._pipelines = dict(pipelines or {})

    @property
    def method(self) -> str:
        return "spacy-ner"

    def method_version(self, language: str) -> str:
        normalized_language = self._normalize_language(language)
        pipeline = self._get_pipeline(normalized_language)
        model_name = self.MODEL_NAMES[normalized_language]
        model_version = str(pipeline.meta.get("version", "unknown"))
        return f"{model_name}@{model_version}"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        if not text.strip():
            raise ValueError("Text must not be blank.")
        normalized_language = self._normalize_language(language)
        document = self._get_pipeline(normalized_language)(text)
        mentions = tuple(
            RecognizedEntityMention(
                entity_type=self.LABEL_TYPES.get(
                    span.label_,
                    EntityType.OTHER,
                ),
                source_label=span.label_,
                surface_text=span.text,
                normalized_text=self._normalize_text(span.text),
                start_char=span.start_char,
                end_char=span.end_char,
            )
            for span in document.ents
        )
        return EntityRecognitionResult(
            mentions=mentions,
            quality_limitations=self.QUALITY_LIMITATIONS,
        )

    def _get_pipeline(self, language: str) -> Language:
        pipeline = self._pipelines.get(language)
        if pipeline is None:
            pipeline = spacy.load(self.MODEL_NAMES[language])
            self._pipelines[language] = pipeline
        return pipeline

    @classmethod
    def _normalize_language(cls, language: str) -> str:
        normalized = language.strip().lower().split("-", maxsplit=1)[0]
        if normalized not in cls.MODEL_NAMES:
            supported = ", ".join(sorted(cls.MODEL_NAMES))
            raise ValueError(
                f"Unsupported entity-recognition language: {language!r}; "
                f"supported languages: {supported}."
            )
        return normalized

    @staticmethod
    def _normalize_text(text: str) -> str:
        unicode_normalized = unicodedata.normalize("NFKC", text)
        return re.sub(r"\s+", " ", unicode_normalized).strip().casefold()
