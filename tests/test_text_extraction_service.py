import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.extraction import ExtractedText
from argus.models import DerivedArtifact
from argus.services.text_extraction_service import TextExtractionService
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class StubExtractor:
    method = "stub-extractor"
    method_version = "1.2.3"

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str | None]] = []

    def extract(self, content: bytes, *, media_type: str | None) -> ExtractedText:
        self.calls.append((content, media_type))
        return ExtractedText(
            "Normalized document text",
            ("Example limitation.",),
        )


class TextExtractionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/article",
            document_type=DocumentType.ARTICLE,
        )
        stored = self.store.store(b"<html><body>source</body></html>")
        raw = RawArtifactRepository(self.session).get_or_create(stored)
        self.version = DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=raw,
            media_type="text/html; charset=utf-8",
        )
        self.extractor = StubExtractor()
        self.service = TextExtractionService(
            self.session,
            artifact_store=self.store,
            extractor=self.extractor,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_extract_reads_stored_bytes_and_registers_artifact(self) -> None:
        artifact = self.service.extract(self.version)

        self.assertEqual(
            artifact.artifact_type,
            DerivedArtifactType.EXTRACTED_TEXT,
        )
        self.assertEqual(artifact.method, "stub-extractor")
        self.assertEqual(artifact.method_version, "1.2.3")
        self.assertEqual(artifact.schema_version, "1")
        self.assertEqual(
            artifact.payload,
            {"character_count": 24, "text": "Normalized document text"},
        )
        self.assertEqual(artifact.quality_limitations, ["Example limitation."])
        self.assertEqual(
            self.extractor.calls,
            [(b"<html><body>source</body></html>", "text/html; charset=utf-8")],
        )

    def test_extract_is_idempotent_for_same_version_and_method(self) -> None:
        first = self.service.extract(self.version)
        second = self.service.extract(self.version)

        self.assertIs(first, second)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(DerivedArtifact)),
            1,
        )

    def test_extract_does_not_commit(self) -> None:
        self.service.extract(self.version)
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(DerivedArtifact)),
            0,
        )

    def test_extract_rejects_unpersisted_version(self) -> None:
        from argus.models import DocumentVersion

        with self.assertRaisesRegex(ValueError, "persisted"):
            self.service.extract(
                DocumentVersion(document_id=1, raw_artifact_id=1, version_number=1)
            )

    def test_extract_rejects_wrong_storage_backend(self) -> None:
        from argus.models import RawArtifact

        stored_raw = self.session.get(RawArtifact, self.version.raw_artifact_id)
        stored_raw.storage_backend = "different-store"
        self.session.flush()

        with self.assertRaisesRegex(ValueError, "backend"):
            self.service.extract(self.version)


if __name__ == "__main__":
    unittest.main()
