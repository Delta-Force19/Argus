import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    EntityRecognitionResult,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import DerivedArtifact, EntityMention
from argus.services.entity_mention_batch_runner import (
    EntityMentionBatchItemStatus,
    EntityMentionBatchRunner,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class SelectiveRecognizer:
    method = "stub-ner"

    def method_version(self, language: str) -> str:
        return f"stub-{language}@1"

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        if "broken" in text:
            raise RuntimeError("recognition failed")
        return EntityRecognitionResult(
            mentions=(
                RecognizedEntityMention(
                    entity_type=EntityType.ORGANIZATION,
                    source_label="ORG",
                    surface_text="Argus",
                    normalized_text="argus",
                    start_char=0,
                    end_char=5,
                ),
            )
        )


class EntityMentionBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.artifact_ids = self._seed_artifacts()
        self.runner = EntityMentionBatchRunner(
            self.session_factory,
            recognizer=SelectiveRecognizer(),
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_artifacts(self) -> tuple[int, ...]:
        ids = []
        with self.session_factory() as session:
            for number, text in enumerate(
                    ("Argus works.", "broken text", "Argus continues.")
            ):
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

    def test_batch_commits_each_success_and_continues_after_failure(self) -> None:
        report = self.runner.run(self.artifact_ids)

        self.assertEqual(report.total_count, 3)
        self.assertEqual(report.processed_count, 2)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.mention_count, 2)
        self.assertEqual(
            [item.status for item in report.items],
            [
                EntityMentionBatchItemStatus.PROCESSED,
                EntityMentionBatchItemStatus.FAILED,
                EntityMentionBatchItemStatus.PROCESSED,
            ],
        )
        self.assertEqual(report.items[1].error_type, "RuntimeError")

        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(EntityMention)
                ),
                2,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(DerivedArtifact)
                    .where(
                        DerivedArtifact.artifact_type
                        == DerivedArtifactType.ENTITY_MENTIONS
                    )
                ),
                2,
            )

    def test_missing_artifact_is_reported_without_raising(self) -> None:
        report = self.runner.run((999,))

        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.items[0].error_type, "LookupError")
        self.assertIn(
            "does not exist",
            report.items[0].error_message,
        )


if __name__ == "__main__":
    unittest.main()
