from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DocumentType
from argus.models import DerivedArtifact
from argus.services.transcript_ingestion_service import (
    TranscriptIngestionService,
)
from argus.services.transcript_timeline_service import (
    inspect_transcript_timeline,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository
from argus.transcripts import TranscriptFormat, TranscriptKind


class TranscriptTimelineServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.temporary_directory = TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )
        with self.session_factory() as session:
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value="https://example.test/video/bulletin",
                document_type=DocumentType.OTHER,
            )
            page_hash = hashlib.sha256(b"page").hexdigest()
            page_raw = RawArtifactRepository(session).get_or_create(
                StoredArtifact(
                    storage_backend="filesystem",
                    storage_key=f"sha256/{page_hash[:2]}/{page_hash[2:]}",
                    hash_algorithm="sha256",
                    content_hash=page_hash,
                    byte_size=4,
                )
            )
            version = DocumentVersionRepository(session).register(
                document=document,
                raw_artifact=page_raw,
                media_type="text/html",
            )
            self.document_version_id = version.id
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_exposes_validated_cue_timeline_and_output_offsets(self) -> None:
        artifact_id = self._ingest(
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nOne.\n\n"
            b"00:00:01.200 --> 00:00:02.000\nTwo.\n"
        )

        report = inspect_transcript_timeline(
            document_version_id=self.document_version_id,
            transcript_artifact_id=artifact_id,
            session_factory=self.session_factory,
            artifact_store=self.store,
        )

        self.assertEqual(report.character_count, 9)
        self.assertEqual(report.cue_provenance_schema_version, "1")
        self.assertEqual(report.contributing_cue_count, 2)
        self.assertEqual(report.suppressed_cue_count, 0)
        first, second = report.items
        self.assertEqual(first.output_text, "One.")
        self.assertEqual(first.output_start_char, 0)
        self.assertEqual(first.output_end_char, 4)
        self.assertIsNone(first.gap_before_ms)
        self.assertEqual(second.output_text, "Two.")
        self.assertEqual(second.output_start_char, 5)
        self.assertEqual(second.output_end_char, 9)
        self.assertEqual(second.gap_before_ms, 200)

    def test_exposes_suppressed_technical_relay_without_fake_span(self) -> None:
        artifact_id = self._ingest(
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nOne two\n\n"
            b"00:00:01.000 --> 00:00:01.010\nOne two\n\n"
            b"00:00:01.010 --> 00:00:02.000\n"
            b"One two\nthree<00:00:01.500><c> four</c>\n"
        )

        report = inspect_transcript_timeline(
            document_version_id=self.document_version_id,
            transcript_artifact_id=artifact_id,
            session_factory=self.session_factory,
            artifact_store=self.store,
        )

        self.assertEqual(report.suppressed_cue_count, 1)
        relay = report.items[1]
        self.assertEqual(relay.suppression_reason, "technical_relay")
        self.assertIsNone(relay.output_start_char)
        self.assertEqual(relay.output_text, "")

    def test_rejects_plain_text_without_cue_provenance(self) -> None:
        artifact_id = self._ingest(
            b"One complete transcript.",
            transcript_format=TranscriptFormat.PLAIN_TEXT,
            media_type="text/plain; charset=utf-8",
        )

        with self.assertRaisesRegex(ValueError, "no cue provenance"):
            inspect_transcript_timeline(
                document_version_id=self.document_version_id,
                transcript_artifact_id=artifact_id,
                session_factory=self.session_factory,
                artifact_store=self.store,
            )

    def test_rejects_tampered_cue_output_span(self) -> None:
        artifact_id = self._ingest(
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nOne.\n"
        )
        with self.session_factory() as session:
            artifact = session.get(DerivedArtifact, artifact_id)
            payload = dict(artifact.payload)
            normalization = dict(payload["normalization"])
            cue_provenance = dict(normalization["cue_provenance"])
            cues = [dict(item) for item in cue_provenance["cues"]]
            cues[0]["output_end_char"] = 3
            cue_provenance["cues"] = cues
            normalization["cue_provenance"] = cue_provenance
            payload["normalization"] = normalization
            artifact.payload = payload
            session.commit()

        with self.assertRaisesRegex(ValueError, "output span conflicts"):
            inspect_transcript_timeline(
                document_version_id=self.document_version_id,
                transcript_artifact_id=artifact_id,
                session_factory=self.session_factory,
                artifact_store=self.store,
            )

    def test_rejects_cue_map_not_anchored_to_raw_block(self) -> None:
        artifact_id = self._ingest(
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nOne.\n"
        )
        with self.session_factory() as session:
            artifact = session.get(DerivedArtifact, artifact_id)
            payload = dict(artifact.payload)
            normalization = dict(payload["normalization"])
            cue_provenance = dict(normalization["cue_provenance"])
            cues = [dict(item) for item in cue_provenance["cues"]]
            cues[0]["source_text_hash"] = "0" * 64
            cue_provenance["cues"] = cues
            normalization["cue_provenance"] = cue_provenance
            payload["normalization"] = normalization
            artifact.payload = payload
            session.commit()

        with self.assertRaisesRegex(ValueError, "source block hash"):
            inspect_transcript_timeline(
                document_version_id=self.document_version_id,
                transcript_artifact_id=artifact_id,
                session_factory=self.session_factory,
                artifact_store=self.store,
            )

    def _ingest(
            self,
            content: bytes,
            *,
            transcript_format: TranscriptFormat = TranscriptFormat.WEBVTT,
            media_type: str = "text/vtt; charset=utf-8",
    ) -> int:
        with self.session_factory() as session:
            result = TranscriptIngestionService(
                session,
                artifact_store=self.store,
            ).ingest(
                document_version_id=self.document_version_id,
                content=content,
                provider="test-provider",
                provider_version="1.2.3",
                requested_location="https://video.test/watch/42",
                resolved_location="https://captions.test/track-en.vtt",
                external_identifier="track-en",
                retrieved_at=datetime(
                    2026, 8, 5, 18, 0, tzinfo=timezone.utc
                ),
                language="en",
                transcript_kind=TranscriptKind.AUTO_GENERATED,
                transcript_format=transcript_format,
                media_type=media_type,
            )
            session.commit()
            return result.transcript_artifact_id


if __name__ == "__main__":
    unittest.main()
