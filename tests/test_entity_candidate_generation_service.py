import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.canonicalizers import (
    DeterministicEntityCandidateCanonicalizer,
)
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    EntityRecognitionResult,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import DerivedArtifact, EntityCandidate
from argus.services.entity_candidate_generation_service import (
    EntityCandidateGenerationService,
)
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
from argus.storage.entity_candidate_repository import (
    EntityCandidateRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class StubRecognizer:
    method = "stub-ner"

    def __init__(self, result: EntityRecognitionResult) -> None:
        self.result = result

    def method_version(self, language: str) -> str:
        return f"stub-{language}@1"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        return self.result


class VersionedCanonicalizer(
        DeterministicEntityCandidateCanonicalizer
):
    def __init__(self, version: str) -> None:
        self.version = version

    @property
    def method_version(self) -> str:
        return self.version


class EntityCandidateGenerationServiceTests(unittest.TestCase):
    TEXT = "António Guterres addressed the UN on Thursday."

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/entity-candidates",
            document_type=DocumentType.ARTICLE,
            language="en",
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/aa/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash="a" * 64,
                byte_size=len(self.TEXT),
            )
        )
        self.version = DocumentVersionRepository(
            self.session
        ).register(document=document, raw_artifact=raw)
        self.text_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method="stub-text",
            method_version="1",
            schema_version="1",
            payload={
                "text": self.TEXT,
                "character_count": len(self.TEXT),
            },
        )
        recognition = EntityRecognitionResult(
            mentions=(
                self._mention(
                    EntityType.PERSON,
                    "PERSON",
                    "António Guterres",
                ),
                self._mention(
                    EntityType.ORGANIZATION,
                    "ORG",
                    "UN",
                ),
                self._mention(
                    EntityType.DATE,
                    "DATE",
                    "Thursday",
                ),
            )
        )
        self.mention_artifact = EntityMentionExtractionService(
            self.session,
            recognizer=StubRecognizer(recognition),
        ).extract(self.text_artifact).artifact
        self.session.commit()
        self.service = EntityCandidateGenerationService(
            self.session,
            canonicalizer=(
                DeterministicEntityCandidateCanonicalizer()
            ),
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _mention(
            self,
            entity_type: EntityType,
            source_label: str,
            surface_text: str,
    ) -> RecognizedEntityMention:
        start = self.TEXT.index(surface_text)
        return RecognizedEntityMention(
            entity_type=entity_type,
            source_label=source_label,
            surface_text=surface_text,
            normalized_text=surface_text.casefold(),
            start_char=start,
            end_char=start + len(surface_text),
        )

    def test_generates_versioned_artifact_and_candidate_projection(
            self,
    ) -> None:
        generation = self.service.generate(self.mention_artifact)

        self.assertEqual(
            generation.artifact.artifact_type,
            DerivedArtifactType.ENTITY_CANDIDATES,
        )
        self.assertEqual(
            generation.artifact.payload["input_artifact_id"],
            self.mention_artifact.id,
        )
        self.assertEqual(
            [item.canonical_text for item in generation.candidates],
            ["antónio guterres", "un"],
        )
        decisions = generation.artifact.payload["decisions"]
        self.assertEqual(len(decisions), 3)
        self.assertEqual(
            decisions[2]["exclusion_reason"],
            "value_or_temporal",
        )
        self.assertFalse(decisions[2]["is_candidate"])

    def test_context_is_exact_and_anchored_to_source_text(self) -> None:
        generation = self.service.generate(self.mention_artifact)

        for candidate in generation.candidates:
            self.assertEqual(candidate.context_start_char, 0)
            self.assertEqual(
                candidate.context_end_char,
                len(self.TEXT),
            )
            self.assertEqual(candidate.context_text, self.TEXT)

    def test_generation_is_idempotent(self) -> None:
        first = self.service.generate(self.mention_artifact)
        second = self.service.generate(self.mention_artifact)

        self.assertEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(
            [item.id for item in first.candidates],
            [item.id for item in second.candidates],
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count())
                .select_from(DerivedArtifact)
                .where(
                    DerivedArtifact.artifact_type
                    == DerivedArtifactType.ENTITY_CANDIDATES
                )
            ),
            1,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(EntityCandidate)
            ),
            2,
        )

    def test_new_canonicalizer_version_preserves_previous_result(
            self,
    ) -> None:
        first = self.service.generate(self.mention_artifact)
        second = EntityCandidateGenerationService(
            self.session,
            canonicalizer=VersionedCanonicalizer("2"),
        ).generate(self.mention_artifact)

        self.assertNotEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(EntityCandidate)
            ),
            4,
        )

    def test_generation_does_not_commit(self) -> None:
        self.service.generate(self.mention_artifact)
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(EntityCandidate)
            ),
            0,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count())
                .select_from(DerivedArtifact)
                .where(
                    DerivedArtifact.artifact_type
                    == DerivedArtifactType.ENTITY_CANDIDATES
                )
            ),
            0,
        )

    def test_repository_queries_candidates_by_document_version(
            self,
    ) -> None:
        generation = self.service.generate(self.mention_artifact)

        candidates = EntityCandidateRepository(
            self.session
        ).get_for_document_version(self.version.id)

        self.assertEqual(
            [item.id for item in candidates],
            [item.id for item in generation.candidates],
        )

    def test_rejects_non_mention_artifact(self) -> None:
        with self.assertRaisesRegex(ValueError, "entity mentions"):
            self.service.generate(self.text_artifact)


if __name__ == "__main__":
    unittest.main()
