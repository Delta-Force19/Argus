import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import (
    DerivedArtifactConflict,
    DerivedArtifactType,
    DocumentType,
)
from argus.models import DerivedArtifact, DocumentVersion
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class DerivedArtifactRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/document",
            document_type=DocumentType.ARTICLE,
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/aa/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash="a" * 64,
                byte_size=128,
            )
        )
        self.version = DocumentVersionRepository(
            self.session
        ).register(document=document, raw_artifact=raw)
        self.repository = DerivedArtifactRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _register(self, **overrides) -> DerivedArtifact:
        arguments = {
            "document_version": self.version,
            "artifact_type": DerivedArtifactType.EXTRACTED_TEXT,
            "method": "trafilatura",
            "method_version": "2.1.0",
            "schema_version": "1",
            "payload": {"text": "Example text", "language": "en"},
            "quality_limitations": ["Boilerplate removal is heuristic."],
        }
        arguments.update(overrides)
        return self.repository.register(**arguments)

    def test_register_creates_artifact_without_commit(self) -> None:
        artifact = self._register()

        self.assertIsNotNone(artifact.id)
        self.assertEqual(len(artifact.content_hash), 64)
        self.session.rollback()

        count = self.session.scalar(
            select(func.count()).select_from(DerivedArtifact)
        )
        self.assertEqual(count, 0)

    def test_register_is_idempotent_for_canonical_payload(self) -> None:
        first = self._register()
        second = self._register(
            payload={"language": "en", "text": "Example text"}
        )

        self.assertIs(first, second)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            ),
            1,
        )

    def test_register_keeps_changed_outputs_distinct(self) -> None:
        first = self._register()
        second = self._register(payload={"text": "Corrected text"})

        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_register_rejects_conflicting_limitations(self) -> None:
        self._register()

        with self.assertRaisesRegex(
            DerivedArtifactConflict, "limitations"
        ):
            self._register(quality_limitations=["Different limitation."])

    def test_register_requires_persisted_document_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "persisted"):
            self._register(
                document_version=DocumentVersion(
                    document_id=1,
                    raw_artifact_id=1,
                    version_number=1,
                )
            )

    def test_register_rejects_blank_method_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "method"):
            self._register(method=" ")

    def test_register_rejects_non_json_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            self._register(payload={"invalid": object()})

    def test_get_for_version_filters_by_artifact_type(self) -> None:
        extracted = self._register()
        self._register(
            artifact_type=DerivedArtifactType.NORMALIZED_METADATA,
            payload={"title": "Example"},
        )

        results = self.repository.get_for_version(
            self.version.id,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
        )

        self.assertEqual([artifact.id for artifact in results], [extracted.id])


if __name__ == "__main__":
    unittest.main()
