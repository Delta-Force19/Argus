from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.models import DerivedArtifact, RawArtifact, TranscriptAcquisition
from argus.services.transcript_ingestion_service import (
    TranscriptIngestionService,
)
from argus.services.transcript_provenance_service import (
    transcript_provenance_issue,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository
from argus.transcripts import TranscriptFormat, TranscriptKind


class TranscriptIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.test/video/bulletin",
            document_type=DocumentType.OTHER,
        )
        page_hash = hashlib.sha256(b"page").hexdigest()
        page_raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key=f"sha256/{page_hash[:2]}/{page_hash[2:]}",
                hash_algorithm="sha256",
                content_hash=page_hash,
                byte_size=4,
            )
        )
        self.version = DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=page_raw,
            media_type="text/html",
        )
        self.temporary_directory = TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )
        self.service = TranscriptIngestionService(
            self.session,
            artifact_store=self.store,
        )
        self.now = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_ingests_webvtt_with_exact_raw_provenance(self) -> None:
        content = (
            b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
            b"First <b>story</b>.\n\n"
            b"cue-2\n00:00:02.000 --> 00:00:04.000\nSecond story.\n"
        )

        result = self._ingest(content)

        acquisition = self.session.get(
            TranscriptAcquisition, result.transcript_acquisition_id
        )
        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(acquisition.raw_artifact_id, result.raw_artifact_id)
        self.assertEqual(acquisition.external_identifier, "track-en")
        self.assertEqual(artifact.artifact_type, DerivedArtifactType.TRANSCRIPT)
        self.assertEqual(
            artifact.payload["text"], "First story.\n\nSecond story."
        )
        self.assertEqual(
            artifact.payload["source"]["content_hash"],
            result.raw_content_hash,
        )
        self.assertEqual(self.store.read(
            self.session.get(
                RawArtifact,
                result.raw_artifact_id,
            ).storage_key
        ), content)

    def test_same_acquisition_is_idempotent(self) -> None:
        first = self._ingest(
            b"One complete transcript.",
            transcript_format=TranscriptFormat.PLAIN_TEXT,
            media_type="text/plain; charset=utf-8",
        )
        second = self._ingest(
            b"One complete transcript.",
            transcript_format=TranscriptFormat.PLAIN_TEXT,
            media_type="text/plain; charset=utf-8",
        )

        self.assertEqual(
            first.transcript_acquisition_id,
            second.transcript_acquisition_id,
        )
        self.assertEqual(first.transcript_artifact_id, second.transcript_artifact_id)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(TranscriptAcquisition)
            ),
            1,
        )

    def test_detects_tampered_raw_digest_reference(self) -> None:
        result = self._ingest(
            b"Complete transcript.",
            transcript_format=TranscriptFormat.PLAIN_TEXT,
            media_type="text/plain; charset=utf-8",
        )
        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        artifact.payload = {
            **artifact.payload,
            "source": {
                **artifact.payload["source"],
                "content_hash": "0" * 64,
            },
        }

        self.assertEqual(
            transcript_provenance_issue(self.session, artifact),
            "Transcript source digest conflicts with its raw artifact.",
        )

    def test_rejects_invalid_utf8_before_storing(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self._ingest(b"\xff\xfe")

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(TranscriptAcquisition)
            ),
            0,
        )

    def test_rejects_unknown_document_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist: 999"):
            self._ingest(b"Text", document_version_id=999)

    def test_rejects_naive_retrieval_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self._ingest(
                b"Text",
                retrieved_at=datetime(2026, 8, 3, 18, 0),
                transcript_format=TranscriptFormat.PLAIN_TEXT,
                media_type="text/plain; charset=utf-8",
            )

    def _ingest(
            self,
            content: bytes,
            *,
            document_version_id: int | None = None,
            transcript_format: TranscriptFormat = TranscriptFormat.WEBVTT,
            media_type: str = "text/vtt; charset=utf-8",
            retrieved_at: datetime | None = None,
    ):
        return self.service.ingest(
            document_version_id=(
                self.version.id
                if document_version_id is None
                else document_version_id
            ),
            content=content,
            provider="test-provider",
            provider_version="1.2.3",
            requested_location="https://video.test/watch/42",
            resolved_location="https://captions.test/track-en.vtt",
            external_identifier="track-en",
            retrieved_at=retrieved_at or self.now,
            language="en",
            transcript_kind=TranscriptKind.AUTO_GENERATED,
            transcript_format=transcript_format,
            media_type=media_type,
        )


if __name__ == "__main__":
    unittest.main()
