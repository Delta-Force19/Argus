import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    EntityRecognitionResult,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import DerivedArtifact, EntityMention
from argus.services.entity_mention_extraction_service import (
    EntityMentionExtractionService,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.entity_mention_repository import (
    EntityMentionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class StubRecognizer:
    method = "stub-ner"

    def __init__(
            self,
            result: EntityRecognitionResult,
    ) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def method_version(self, language: str) -> str:
        return f"stub-{language}@1"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        self.calls.append((text, language))
        return self.result


class EntityMentionExtractionServiceTests(unittest.TestCase):
    TEXT = "Анна работает в МГУ."

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/ru",
            document_type=DocumentType.ARTICLE,
            language="ru-RU",
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/aa/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash="a" * 64,
                byte_size=100,
            )
        )
        version = DocumentVersionRepository(
            self.session
        ).register(document=document, raw_artifact=raw)
        self.text_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method="stub-text",
            method_version="1",
            schema_version="1",
            payload={
                "text": self.TEXT,
                "character_count": len(self.TEXT),
            },
        )
        self.result = EntityRecognitionResult(
            mentions=(
                RecognizedEntityMention(
                    entity_type=EntityType.PERSON,
                    source_label="PER",
                    surface_text="Анна",
                    normalized_text="анна",
                    start_char=0,
                    end_char=4,
                ),
                RecognizedEntityMention(
                    entity_type=EntityType.ORGANIZATION,
                    source_label="ORG",
                    surface_text="МГУ",
                    normalized_text="мгу",
                    start_char=16,
                    end_char=19,
                ),
            ),
            quality_limitations=("Statistical model.",),
        )
        self.recognizer = StubRecognizer(self.result)
        self.service = EntityMentionExtractionService(
            self.session,
            recognizer=self.recognizer,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_extract_persists_reproducible_artifact_and_mentions(self) -> None:
        extraction = self.service.extract(self.text_artifact)

        self.assertEqual(
            extraction.artifact.artifact_type,
            DerivedArtifactType.ENTITY_MENTIONS,
        )
        self.assertEqual(extraction.artifact.method, "stub-ner")
        self.assertEqual(
            extraction.artifact.method_version,
            "stub-ru@1",
        )
        self.assertEqual(
            extraction.artifact.payload["input_artifact_id"],
            self.text_artifact.id,
        )
        self.assertEqual(
            extraction.artifact.payload["input_content_hash"],
            self.text_artifact.content_hash,
        )
        self.assertEqual(extraction.artifact.payload["language"], "ru")
        self.assertEqual(
            [mention.surface_text for mention in extraction.mentions],
            ["Анна", "МГУ"],
        )
        self.assertTrue(
            all(
                mention.document_version_id
                == self.text_artifact.document_version_id
                for mention in extraction.mentions
            )
        )
        self.assertEqual(
            self.recognizer.calls,
            [(self.TEXT, "ru")],
        )

    def test_extract_is_idempotent(self) -> None:
        first = self.service.extract(self.text_artifact)
        second = self.service.extract(self.text_artifact)

        self.assertEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(
            [mention.id for mention in first.mentions],
            [mention.id for mention in second.mentions],
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            ),
            2,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(EntityMention)
            ),
            2,
        )

    def test_extract_does_not_commit(self) -> None:
        self.service.extract(self.text_artifact)
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(EntityMention)
            ),
            0,
        )

    def test_explicit_language_overrides_document_language(self) -> None:
        extraction = self.service.extract(
            self.text_artifact,
            language="en-GB",
        )

        self.assertEqual(extraction.artifact.payload["language"], "en")
        self.assertEqual(self.recognizer.calls, [(self.TEXT, "en")])

    def test_rejects_offsets_that_do_not_match_surface_text(self) -> None:
        self.recognizer.result = EntityRecognitionResult(
            mentions=(
                RecognizedEntityMention(
                    entity_type=EntityType.PERSON,
                    source_label="PER",
                    surface_text="Иван",
                    normalized_text="иван",
                    start_char=0,
                    end_char=4,
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            self.service.extract(self.text_artifact)

    def test_repository_queries_mentions_by_document_version(self) -> None:
        extraction = self.service.extract(self.text_artifact)

        mentions = EntityMentionRepository(
            self.session
        ).get_for_document_version(
            self.text_artifact.document_version_id
        )

        self.assertEqual(
            [mention.id for mention in mentions],
            [mention.id for mention in extraction.mentions],
        )


if __name__ == "__main__":
    unittest.main()
