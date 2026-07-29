import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from argus.models import DerivedArtifact
from argus.services.entity_candidate_generation_service import (
    EntityCandidateGenerationService,
)
from argus.services.entity_candidate_pipeline import (
    _pending_mention_artifact_ids,
    run_entity_candidate_pipeline,
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
from argus.storage.raw_artifact_repository import RawArtifactRepository


class StubRecognizer:
    method = "stub-ner"

    def __init__(
            self,
            entity_type: EntityType = EntityType.ORGANIZATION,
    ) -> None:
        self.entity_type = entity_type

    def method_version(self, language: str) -> str:
        return f"stub-{language}@1"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        return EntityRecognitionResult(
            mentions=(
                RecognizedEntityMention(
                    entity_type=self.entity_type,
                    source_label=self.entity_type.value.upper(),
                    surface_text=text,
                    normalized_text=text.casefold(),
                    start_char=0,
                    end_char=len(text),
                ),
            )
        )


class VersionedCanonicalizer(
        DeterministicEntityCandidateCanonicalizer
):
    def __init__(self, version: str = "1") -> None:
        self.version = version

    @property
    def method_version(self) -> str:
        return self.version


class EntityCandidatePipelineTests(unittest.TestCase):
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
            for number, (text, entity_type) in enumerate((
                    ("Argus", EntityType.ORGANIZATION),
                    ("Thursday", EntityType.DATE),
                    ("Athena", EntityType.PERSON),
            )):
                document = DocumentRepository(session).get_or_create(
                    identifier_scheme="uri",
                    identifier_value=f"https://example.com/{number}",
                    document_type=DocumentType.ARTICLE,
                    language="en",
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
                text_artifact = DerivedArtifactRepository(session).register(
                    document_version=version,
                    artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
                    method="stub-text",
                    method_version="1",
                    schema_version="1",
                    payload={"text": text, "character_count": len(text)},
                )
                mention_artifact = EntityMentionExtractionService(
                    session,
                    recognizer=StubRecognizer(entity_type),
                ).extract(text_artifact).artifact
                ids.append(mention_artifact.id)
            session.commit()
        return tuple(ids)

    def test_selection_is_stable_and_bounded(self) -> None:
        with self.session_factory() as session:
            selected = _pending_mention_artifact_ids(
                session=session,
                canonicalizer=VersionedCanonicalizer(),
                limit=2,
            )

        self.assertEqual(selected, self.artifact_ids[:2])

    def test_selection_skips_matching_output_and_fills_batch(self) -> None:
        canonicalizer = VersionedCanonicalizer()
        with self.session_factory() as session:
            first = session.get(DerivedArtifact, self.artifact_ids[0])
            EntityCandidateGenerationService(
                session,
                canonicalizer=canonicalizer,
            ).generate(first)
            session.commit()

        with self.session_factory() as session:
            selected = _pending_mention_artifact_ids(
                session=session,
                canonicalizer=canonicalizer,
                limit=2,
            )

        self.assertEqual(selected, self.artifact_ids[1:])

    def test_new_canonicalizer_version_requeues_previous_input(self) -> None:
        run_entity_candidate_pipeline(
            limit=1,
            session_factory=self.session_factory,
            canonicalizer=VersionedCanonicalizer("1"),
        )

        with self.session_factory() as session:
            selected = _pending_mention_artifact_ids(
                session=session,
                canonicalizer=VersionedCanonicalizer("2"),
                limit=1,
            )

        self.assertEqual(selected, (self.artifact_ids[0],))

    def test_repeated_runs_advance_queue_and_count_exclusions(self) -> None:
        canonicalizer = VersionedCanonicalizer()

        first = run_entity_candidate_pipeline(
            limit=2,
            session_factory=self.session_factory,
            canonicalizer=canonicalizer,
        )
        second = run_entity_candidate_pipeline(
            limit=2,
            session_factory=self.session_factory,
            canonicalizer=canonicalizer,
        )
        third = run_entity_candidate_pipeline(
            limit=2,
            session_factory=self.session_factory,
            canonicalizer=canonicalizer,
        )

        self.assertEqual(first.processed_count, 2)
        self.assertEqual(first.candidate_count, 1)
        self.assertEqual(first.excluded_count, 1)
        self.assertEqual(second.processed_count, 1)
        self.assertEqual(second.candidate_count, 1)
        self.assertEqual(third.total_count, 0)

    def test_rejects_non_positive_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_entity_candidate_pipeline(
                limit=0,
                session_factory=self.session_factory,
                canonicalizer=VersionedCanonicalizer(),
            )


if __name__ == "__main__":
    unittest.main()
