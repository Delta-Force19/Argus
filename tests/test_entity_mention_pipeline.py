import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import EntityRecognitionResult
from argus.models import DerivedArtifact
from argus.services.entity_mention_extraction_service import (
    EntityMentionExtractionService,
)
from argus.services.entity_mention_pipeline import (
    _pending_text_artifact_ids,
    run_entity_mention_pipeline,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class VersionedRecognizer:
    method = "stub-ner"

    def __init__(
            self,
            *,
            version: str = "1",
            supported_languages: tuple[str, ...] = ("en", "ru"),
    ) -> None:
        self.version = version
        self.supported_languages = supported_languages
        self.calls: list[tuple[str, str]] = []

    def method_version(self, language: str) -> str:
        if language not in self.supported_languages:
            raise ValueError("Unsupported language")
        return f"stub-{language}@{self.version}"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        self.calls.append((text, language))
        return EntityRecognitionResult(mentions=())


class EntityMentionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.artifact_ids = self._seed_artifacts()

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_artifacts(self) -> tuple[int, ...]:
        ids = []
        with self.session_factory() as session:
            for number, language in enumerate(("en-US", "de", "ru-RU")):
                text = f"Text {number}"
                document = DocumentRepository(session).get_or_create(
                    identifier_scheme="uri",
                    identifier_value=f"https://example.com/{number}",
                    document_type=DocumentType.ARTICLE,
                    language=language,
                )
                raw = RawArtifactRepository(session).get_or_create(
                    StoredArtifact(
                        storage_backend="filesystem",
                        storage_key=f"sha256/{number}/" + str(number) * 64,
                        hash_algorithm="sha256",
                        content_hash=str(number) * 64,
                        byte_size=len(text),
                    )
                )
                version = DocumentVersionRepository(session).register(
                    document=document,
                    raw_artifact=raw,
                )
                artifact = DerivedArtifactRepository(session).register(
                    document_version=version,
                    artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
                    method="stub-text",
                    method_version="1",
                    schema_version="1",
                    payload={
                        "text": text,
                        "character_count": len(text),
                    },
                )
                ids.append(artifact.id)
            session.commit()
        return tuple(ids)

    def test_selection_skips_unsupported_languages_without_using_limit(self) -> None:
        with self.session_factory() as session:
            selected = _pending_text_artifact_ids(
                session=session,
                recognizer=VersionedRecognizer(),
                limit=2,
            )

        self.assertEqual(
            selected,
            (self.artifact_ids[0], self.artifact_ids[2]),
        )

    def test_selection_skips_matching_output_and_fills_batch(self) -> None:
        recognizer = VersionedRecognizer()
        with self.session_factory() as session:
            first = session.get(DerivedArtifact, self.artifact_ids[0])
            EntityMentionExtractionService(
                session,
                recognizer=recognizer,
            ).extract(first)
            session.commit()

        with self.session_factory() as session:
            selected = _pending_text_artifact_ids(
                session=session,
                recognizer=recognizer,
                limit=1,
            )

        self.assertEqual(selected, (self.artifact_ids[2],))

    def test_new_model_version_selects_previously_processed_input(self) -> None:
        run_entity_mention_pipeline(
            limit=1,
            session_factory=self.session_factory,
            recognizer=VersionedRecognizer(version="1"),
        )

        with self.session_factory() as session:
            selected = _pending_text_artifact_ids(
                session=session,
                recognizer=VersionedRecognizer(version="2"),
                limit=1,
            )

        self.assertEqual(selected, (self.artifact_ids[0],))

    def test_pipeline_is_bounded_and_repeated_run_advances_queue(self) -> None:
        recognizer = VersionedRecognizer()

        first = run_entity_mention_pipeline(
            limit=1,
            session_factory=self.session_factory,
            recognizer=recognizer,
        )
        second = run_entity_mention_pipeline(
            limit=1,
            session_factory=self.session_factory,
            recognizer=recognizer,
        )
        third = run_entity_mention_pipeline(
            limit=1,
            session_factory=self.session_factory,
            recognizer=recognizer,
        )

        self.assertEqual(first.processed_count, 1)
        self.assertEqual(second.processed_count, 1)
        self.assertEqual(third.total_count, 0)
        self.assertEqual(
            recognizer.calls,
            [("Text 0", "en"), ("Text 2", "ru")],
        )

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_entity_mention_pipeline(
                limit=0,
                session_factory=self.session_factory,
                recognizer=VersionedRecognizer(),
            )


if __name__ == "__main__":
    unittest.main()
