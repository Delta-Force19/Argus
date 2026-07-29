import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import (
    DocumentIdentityConflict,
    DocumentType,
    DocumentVersionConflict,
)
from argus.models import Document, DocumentVersion, RawArtifact
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import (
    RawArtifactRepository,
)


class DocumentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = DocumentRepository(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_get_or_create_adds_document_without_commit(
            self,
    ) -> None:
        document = self.repository.get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/report",
            document_type=DocumentType.REPORT,
            title="Example report",
            language="en",
        )

        self.assertIsNotNone(document.id)
        self.assertEqual(document.document_type, DocumentType.REPORT)

        self.session.rollback()

        self.assertIsNone(
            self.repository.get_by_identity(
                identifier_scheme="uri",
                identifier_value="https://example.com/report",
            )
        )

    def test_get_or_create_returns_same_stable_identity(
            self,
    ) -> None:
        first = self.repository.get_or_create(
            identifier_scheme="doi",
            identifier_value="10.1000/example",
            document_type=DocumentType.SCIENTIFIC_WORK,
        )
        second = self.repository.get_or_create(
            identifier_scheme="doi",
            identifier_value="10.1000/example",
            document_type=DocumentType.SCIENTIFIC_WORK,
            title="Later metadata",
            language="en",
        )
        count = self.session.scalar(
            select(func.count()).select_from(Document)
        )

        self.assertIs(first, second)
        self.assertEqual(count, 1)
        self.assertEqual(second.title, "Later metadata")
        self.assertEqual(second.language, "en")

    def test_get_or_create_rejects_identity_conflict(
            self,
    ) -> None:
        self.repository.get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/item",
            document_type=DocumentType.ARTICLE,
        )

        with self.assertRaisesRegex(
            DocumentIdentityConflict,
            "document_type",
        ):
            self.repository.get_or_create(
                identifier_scheme="uri",
                identifier_value="https://example.com/item",
                document_type=DocumentType.DATASET,
            )

    def test_get_or_create_rejects_blank_identity(
            self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "scheme"):
            self.repository.get_or_create(
                identifier_scheme=" ",
                identifier_value="value",
                document_type=DocumentType.OTHER,
            )


class DocumentVersionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.document_repository = DocumentRepository(
            self.session
        )
        self.version_repository = DocumentVersionRepository(
            self.session
        )
        self.artifact_repository = RawArtifactRepository(
            self.session
        )
        self.document = self.document_repository.get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/document",
            document_type=DocumentType.ARTICLE,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _artifact(self, digest_byte: str) -> RawArtifact:
        content_hash = digest_byte * 64
        return self.artifact_repository.get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key=(
                    f"sha256/{content_hash[:2]}/{content_hash}"
                ),
                hash_algorithm="sha256",
                content_hash=content_hash,
                byte_size=128,
            )
        )

    def test_register_creates_first_version_without_commit(
            self,
    ) -> None:
        artifact = self._artifact("a")
        version = self.version_repository.register(
            document=self.document,
            raw_artifact=artifact,
            media_type="text/html",
        )

        self.assertIsNotNone(version.id)
        self.assertEqual(version.version_number, 1)

        self.session.rollback()

        count = self.session.scalar(
            select(func.count()).select_from(DocumentVersion)
        )
        self.assertEqual(count, 0)

    def test_register_is_idempotent_for_same_artifact(
            self,
    ) -> None:
        artifact = self._artifact("b")
        published_at = datetime(
            2026,
            7,
            21,
            12,
            tzinfo=timezone.utc,
        )
        first = self.version_repository.register(
            document=self.document,
            raw_artifact=artifact,
            media_type="application/pdf",
            published_at=published_at,
        )
        second = self.version_repository.register(
            document=self.document,
            raw_artifact=artifact,
            media_type="application/pdf",
            published_at=published_at,
        )

        self.assertIs(first, second)
        self.assertEqual(
            len(
                self.version_repository.get_versions(
                    self.document.id
                )
            ),
            1,
        )

    def test_register_assigns_monotonic_version_numbers(
            self,
    ) -> None:
        first = self.version_repository.register(
            document=self.document,
            raw_artifact=self._artifact("c"),
        )
        second = self.version_repository.register(
            document=self.document,
            raw_artifact=self._artifact("d"),
        )

        self.assertEqual(first.version_number, 1)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(
            [
                version.id
                for version in self.version_repository.get_versions(
                    self.document.id
                )
            ],
            [first.id, second.id],
        )

    def test_register_rejects_metadata_conflict(
            self,
    ) -> None:
        artifact = self._artifact("e")
        self.version_repository.register(
            document=self.document,
            raw_artifact=artifact,
            media_type="text/html",
        )

        with self.assertRaisesRegex(
            DocumentVersionConflict,
            "media_type",
        ):
            self.version_repository.register(
                document=self.document,
                raw_artifact=artifact,
                media_type="application/pdf",
            )

    def test_register_requires_persisted_inputs(
            self,
    ) -> None:
        artifact = self._artifact("f")

        with self.assertRaisesRegex(ValueError, "document"):
            self.version_repository.register(
                document=Document(
                    document_type=DocumentType.OTHER,
                    identifier_scheme="argus",
                    identifier_value="unpersisted",
                ),
                raw_artifact=artifact,
            )


if __name__ == "__main__":
    unittest.main()
