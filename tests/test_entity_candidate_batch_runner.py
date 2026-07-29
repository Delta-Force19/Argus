import unittest

from sqlalchemy import create_engine, func, select
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
from argus.models import DerivedArtifact, EntityCandidate
from argus.services.entity_candidate_batch_runner import (
    EntityCandidateBatchItemStatus,
    EntityCandidateBatchRunner,
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

    def __init__(self, text: str) -> None:
        self.text = text

    def method_version(self, language: str) -> str:
        return f"stub-{language}@1"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        start = text.index(self.text)
        return EntityRecognitionResult(
            mentions=(
                RecognizedEntityMention(
                    entity_type=EntityType.ORGANIZATION,
                    source_label="ORG",
                    surface_text=self.text,
                    normalized_text=self.text.casefold(),
                    start_char=start,
                    end_char=start + len(self.text),
                ),
            )
        )


class SelectiveCanonicalizer(
        DeterministicEntityCandidateCanonicalizer
):
    def canonicalize(self, *, entity_type, normalized_text):
        if normalized_text == "broken":
            raise RuntimeError("canonicalization failed")
        return super().canonicalize(
            entity_type=entity_type,
            normalized_text=normalized_text,
        )


class EntityCandidateBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.artifact_ids = self._seed_artifacts()
        self.runner = EntityCandidateBatchRunner(
            self.session_factory,
            canonicalizer=SelectiveCanonicalizer(),
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_artifacts(self) -> tuple[int, ...]:
        ids = []
        with self.session_factory() as session:
            for number, name in enumerate(("Argus", "broken", "Athena")):
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
                        byte_size=len(name),
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
                    payload={"text": name, "character_count": len(name)},
                )
                mention_artifact = EntityMentionExtractionService(
                    session,
                    recognizer=StubRecognizer(name),
                ).extract(text_artifact).artifact
                ids.append(mention_artifact.id)
            session.commit()
        return tuple(ids)

    def test_batch_commits_successes_and_continues_after_failure(self) -> None:
        report = self.runner.run(self.artifact_ids)

        self.assertEqual(report.total_count, 3)
        self.assertEqual(report.processed_count, 2)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.candidate_count, 2)
        self.assertEqual(report.excluded_count, 0)
        self.assertEqual(
            [item.status for item in report.items],
            [
                EntityCandidateBatchItemStatus.PROCESSED,
                EntityCandidateBatchItemStatus.FAILED,
                EntityCandidateBatchItemStatus.PROCESSED,
            ],
        )
        self.assertEqual(report.items[1].error_type, "RuntimeError")

        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(EntityCandidate)
                ),
                2,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(DerivedArtifact)
                    .where(
                        DerivedArtifact.artifact_type
                        == DerivedArtifactType.ENTITY_CANDIDATES
                    )
                ),
                2,
            )

    def test_missing_artifact_is_reported_without_raising(self) -> None:
        report = self.runner.run((999,))

        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.items[0].error_type, "LookupError")
        self.assertIn("does not exist", report.items[0].error_message)


if __name__ == "__main__":
    unittest.main()
