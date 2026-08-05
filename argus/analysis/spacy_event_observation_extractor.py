import re
import unicodedata
from collections.abc import Mapping

import spacy
from spacy.language import Language
from spacy.tokens import Span, Token

from argus.event_observations import (
    EventObservationExtractionResult,
    EventObservationType,
    ExtractedEventObservation,
)


class SpacyEventObservationExtractor:
    """Extract source-level event signals without assigning semantic roles."""

    MODEL_NAMES = {
        "en": "en_core_web_sm",
        "ru": "ru_core_news_sm",
    }
    ENTITY_OBSERVATION_TYPES = {
        "PERSON": EventObservationType.PARTICIPANT_MENTION,
        "PER": EventObservationType.PARTICIPANT_MENTION,
        "ORG": EventObservationType.PARTICIPANT_MENTION,
        "NORP": EventObservationType.PARTICIPANT_MENTION,
        "GPE": EventObservationType.PLACE_MENTION,
        "LOC": EventObservationType.PLACE_MENTION,
        "FAC": EventObservationType.PLACE_MENTION,
        "DATE": EventObservationType.TIME_MENTION,
        "TIME": EventObservationType.TIME_MENTION,
        "EVENT": EventObservationType.EVENT_MENTION,
    }
    OBJECT_DEPENDENCIES = frozenset({
        "dobj",
        "obj",
        "attr",
        "oprd",
        "nsubjpass",
        "nsubj:pass",
    })
    QUALITY_LIMITATIONS = (
        "Statistical linguistic analysis can omit or misclassify observations.",
        (
            "Entity mentions are possible event roles, not verified "
            "participants, places, or times."
        ),
        (
            "Action and object candidates are grammatical signals, not "
            "factual subject-predicate-object relations."
        ),
        "Coreference and relations between extracted observations are not resolved.",
    )

    def __init__(
            self,
            *,
            pipelines: Mapping[str, Language] | None = None,
    ) -> None:
        self._pipelines = dict(pipelines or {})

    @property
    def method(self) -> str:
        return "spacy-event-observations"

    def method_version(self, language: str) -> str:
        normalized_language = self._normalize_language(language)
        pipeline = self._get_pipeline(normalized_language)
        model_name = self.MODEL_NAMES[normalized_language]
        model_version = str(pipeline.meta.get("version", "unknown"))
        return f"{model_name}@{model_version}"

    def extract(
            self,
            text: str,
            *,
            language: str,
    ) -> EventObservationExtractionResult:
        if not text.strip():
            raise ValueError("Event observation text must not be blank.")
        normalized_language = self._normalize_language(language)
        document = self._get_pipeline(normalized_language)(text)
        observations: list[ExtractedEventObservation] = []

        for entity in document.ents:
            observation_type = self.ENTITY_OBSERVATION_TYPES.get(
                entity.label_
            )
            if observation_type is None:
                continue
            observations.append(self._entity_observation(
                entity,
                observation_type=observation_type,
            ))

        for token in document:
            if token.pos_ == "VERB" and token.dep_ not in {"aux", "auxpass"}:
                observations.append(self._action_observation(token))
            if token.dep_ in self.OBJECT_DEPENDENCIES:
                observations.append(self._object_observation(token))

        observations.sort(key=lambda item: (
            item.start_char,
            item.end_char,
            item.observation_type.value,
            item.source_label,
        ))
        return EventObservationExtractionResult(
            observations=tuple(observations),
            quality_limitations=self.QUALITY_LIMITATIONS,
        )

    @classmethod
    def _entity_observation(
            cls,
            entity: Span,
            *,
            observation_type: EventObservationType,
    ) -> ExtractedEventObservation:
        return ExtractedEventObservation(
            observation_type=observation_type,
            source_label=entity.label_,
            surface_text=entity.text,
            normalized_value=cls._normalize_text(entity.text),
            start_char=entity.start_char,
            end_char=entity.end_char,
            rationale=(
                f"spaCy named-entity label {entity.label_!r} mapped to "
                f"{observation_type.value!r}."
            ),
        )

    @classmethod
    def _action_observation(cls, token: Token) -> ExtractedEventObservation:
        normalized = cls._normalize_text(token.lemma_ or token.text)
        return ExtractedEventObservation(
            observation_type=EventObservationType.ACTION_CANDIDATE,
            source_label=f"{token.pos_}:{token.dep_}",
            surface_text=token.text,
            normalized_value=normalized,
            start_char=token.idx,
            end_char=token.idx + len(token.text),
            rationale=(
                "Non-auxiliary verbal token retained as a lexical action "
                "candidate."
            ),
        )

    @classmethod
    def _object_observation(cls, token: Token) -> ExtractedEventObservation:
        subtree = tuple(token.subtree)
        start = min(item.idx for item in subtree)
        end = max(item.idx + len(item.text) for item in subtree)
        surface = token.doc.text[start:end]
        return ExtractedEventObservation(
            observation_type=EventObservationType.OBJECT_CANDIDATE,
            source_label=f"{token.pos_}:{token.dep_}",
            surface_text=surface,
            normalized_value=cls._normalize_text(surface),
            start_char=start,
            end_char=end,
            rationale=(
                f"Dependency label {token.dep_!r} retained as a grammatical "
                "object candidate."
            ),
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
                f"Unsupported event-observation language: {language!r}; "
                f"supported languages: {supported}."
            )
        return normalized

    @staticmethod
    def _normalize_text(text: str) -> str:
        unicode_normalized = unicodedata.normalize("NFKC", text)
        return re.sub(r"\s+", " ", unicode_normalized).strip().casefold()
