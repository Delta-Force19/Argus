from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import EntityRecognizer, RecognizedEntityMention
from argus.models import DerivedArtifact, Document, DocumentVersion, EntityMention
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.entity_mention_repository import EntityMentionRepository


@dataclass(frozen=True, slots=True)
class EntityMentionExtraction:
    """Persisted output of one reproducible mention-recognition run."""

    artifact: DerivedArtifact
    mentions: tuple[EntityMention, ...]


class EntityMentionExtractionService:
    """Extract entity mentions from one immutable text artifact.

    The service never commits. The caller owns the surrounding transaction.
    """

    SCHEMA_VERSION = "1"
    TEXT_ARTIFACT_TYPES = {
        DerivedArtifactType.EXTRACTED_TEXT,
        DerivedArtifactType.OCR_TEXT,
        DerivedArtifactType.TRANSCRIPT,
        DerivedArtifactType.TRANSLATION,
    }

    def __init__(
            self,
            session: Session,
            *,
            recognizer: EntityRecognizer,
    ) -> None:
        self._session = session
        self._recognizer = recognizer
        self._artifacts = DerivedArtifactRepository(session)
        self._mentions = EntityMentionRepository(session)

    def extract(
            self,
            text_artifact: DerivedArtifact,
            *,
            language: str | None = None,
    ) -> EntityMentionExtraction:
        text = self._read_text(text_artifact)
        resolved_language = self._resolve_language(
            text_artifact,
            language=language,
        )
        result = self._recognizer.recognize(
            text,
            language=resolved_language,
        )
        self._validate_mentions(text, result.mentions)

        artifact = self._artifacts.register(
            document_version=self._load_version(text_artifact),
            artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
            method=self._recognizer.method,
            method_version=self._recognizer.method_version(
                resolved_language
            ),
            schema_version=self.SCHEMA_VERSION,
            payload={
                "input_artifact_id": text_artifact.id,
                "input_content_hash": text_artifact.content_hash,
                "language": resolved_language,
                "mentions": [
                    {
                        "entity_type": mention.entity_type.value,
                        "source_label": mention.source_label,
                        "surface_text": mention.surface_text,
                        "normalized_text": mention.normalized_text,
                        "start_char": mention.start_char,
                        "end_char": mention.end_char,
                    }
                    for mention in result.mentions
                ],
            },
            quality_limitations=result.quality_limitations,
        )
        mentions = self._mentions.register(
            artifact=artifact,
            mentions=result.mentions,
        )
        return EntityMentionExtraction(
            artifact=artifact,
            mentions=tuple(mentions),
        )

    def _read_text(self, artifact: DerivedArtifact) -> str:
        if artifact.id is None:
            raise ValueError(
                "text_artifact must be persisted before recognition."
            )
        if artifact.artifact_type not in self.TEXT_ARTIFACT_TYPES:
            raise ValueError("text_artifact must contain derived text.")
        text = artifact.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text_artifact payload must contain usable text.")
        return text

    def _load_version(
            self,
            artifact: DerivedArtifact,
    ) -> DocumentVersion:
        version = self._session.get(
            DocumentVersion,
            artifact.document_version_id,
        )
        if version is None:
            raise ValueError("Text artifact document version does not exist.")
        return version

    def _resolve_language(
            self,
            artifact: DerivedArtifact,
            *,
            language: str | None,
    ) -> str:
        if language is not None and language.strip():
            return language.strip().lower().split("-", maxsplit=1)[0]

        version = self._load_version(artifact)
        document = self._session.get(Document, version.document_id)
        if document is None:
            raise ValueError("Text artifact document does not exist.")
        if not document.language or not document.language.strip():
            raise ValueError(
                "Entity recognition requires an explicit document language."
            )
        return document.language.strip().lower().split("-", maxsplit=1)[0]

    @staticmethod
    def _validate_mentions(
            text: str,
            mentions: tuple[RecognizedEntityMention, ...],
    ) -> None:
        previous_key: tuple[int, int] | None = None
        for mention in mentions:
            if mention.end_char > len(text):
                raise ValueError("Entity mention exceeds input text.")
            if text[mention.start_char:mention.end_char] != mention.surface_text:
                raise ValueError(
                    "Entity mention surface text does not match its offsets."
                )
            key = (mention.start_char, mention.end_char)
            if previous_key is not None and key < previous_key:
                raise ValueError(
                    "Entity mentions must be ordered by character offsets."
                )
            previous_key = key
