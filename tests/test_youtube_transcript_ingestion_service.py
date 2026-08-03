from datetime import datetime, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DocumentType
from argus.models import DerivedArtifact, TranscriptAcquisition
from argus.services.youtube_transcript_ingestion_service import (
    ingest_youtube_transcript,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository
from argus.transcript_sources.youtube import (
    RetrievedYouTubeTranscript,
    YouTubeTranscriptCatalog,
    YouTubeTranscriptTrack,
)
from argus.transcripts import TranscriptFormat, TranscriptKind


VIDEO_ID = "dcmdgYtPeTg"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


class _Source:
    def __init__(self) -> None:
        self.calls = []

    def retrieve(self, location, *, track_id, allow_auto_generated=False):
        self.calls.append((location, track_id, allow_auto_generated))
        track = YouTubeTranscriptTrack(
            track_id="en",
            name="English",
            transcript_kind=TranscriptKind.PUBLISHER_PROVIDED,
            transcript_format=TranscriptFormat.WEBVTT,
            media_type="text/vtt; charset=utf-8",
            location="https://captions.test/en.vtt",
        )
        return RetrievedYouTubeTranscript(
            catalog=YouTubeTranscriptCatalog(
                requested_location=location,
                canonical_location=VIDEO_URL,
                video_id=VIDEO_ID,
                title="Latest news bulletin",
                provider="yt-dlp/youtube",
                provider_version="2026.7.4",
                tracks=(track,),
            ),
            track=track,
            content=(
                b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBulletin.\n"
            ),
            resolved_location="https://captions.test/en.vtt?signature=1",
            retrieved_at=datetime(
                2026, 8, 3, 20, 0, tzinfo=timezone.utc
            ),
        )


class YouTubeTranscriptIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.temporary_directory = TemporaryDirectory()
        self.store = FileSystemRawArtifactStore(
            Path(self.temporary_directory.name)
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_same_youtube_document_ingests_without_relation_limitation(self):
        version_id = self._version(VIDEO_URL)
        source = _Source()

        result = ingest_youtube_transcript(
            document_version_id=version_id,
            youtube_url=VIDEO_URL,
            track_id="en",
            source=source,
            session_factory=self.sessions,
            artifact_store=self.store,
        )

        self.assertFalse(result.cross_location)
        with self.sessions() as session:
            acquisition = session.get(
                TranscriptAcquisition,
                result.ingestion.transcript_acquisition_id,
            )
            artifact = session.get(
                DerivedArtifact,
                result.ingestion.transcript_artifact_id,
            )
            self.assertEqual(
                acquisition.external_identifier,
                f"youtube:{VIDEO_ID}:caption:en",
            )
            self.assertFalse(any(
                "operator-asserted" in limitation
                for limitation in artifact.quality_limitations
            ))

    def test_cross_location_requires_explicit_authorization(self) -> None:
        version_id = self._version(
            "https://www.euronews.com/video/2026/07/26/bulletin"
        )
        source = _Source()

        with self.assertRaisesRegex(ValueError, "cross-location"):
            ingest_youtube_transcript(
                document_version_id=version_id,
                youtube_url=VIDEO_URL,
                track_id="en",
                source=source,
                session_factory=self.sessions,
                artifact_store=self.store,
            )

        self.assertEqual(source.calls, [])
        with self.sessions() as session:
            self.assertEqual(
                list(session.scalars(select(TranscriptAcquisition))), []
            )

    def test_cross_location_records_operator_assertion_limitation(self) -> None:
        version_id = self._version(
            "https://www.euronews.com/video/2026/07/26/bulletin"
        )

        result = ingest_youtube_transcript(
            document_version_id=version_id,
            youtube_url=VIDEO_URL,
            track_id="en",
            allow_cross_location=True,
            source=_Source(),
            session_factory=self.sessions,
            artifact_store=self.store,
        )

        self.assertTrue(result.cross_location)
        with self.sessions() as session:
            artifact = session.get(
                DerivedArtifact,
                result.ingestion.transcript_artifact_id,
            )
            self.assertTrue(any(
                "operator-asserted" in limitation
                for limitation in artifact.quality_limitations
            ))

    def test_unknown_version_fails_before_network_access(self) -> None:
        source = _Source()

        with self.assertRaisesRegex(ValueError, "does not exist"):
            ingest_youtube_transcript(
                document_version_id=999,
                youtube_url=VIDEO_URL,
                track_id="en",
                source=source,
                session_factory=self.sessions,
                artifact_store=self.store,
            )

        self.assertEqual(source.calls, [])

    def _version(self, identifier: str) -> int:
        with self.sessions() as session:
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value=identifier,
                document_type=DocumentType.OTHER,
            )
            page_hash = hashlib.sha256(identifier.encode()).hexdigest()
            raw_artifact = RawArtifactRepository(session).get_or_create(
                StoredArtifact(
                    storage_backend="filesystem",
                    storage_key=(
                        f"sha256/{page_hash[:2]}/{page_hash[2:]}"
                    ),
                    hash_algorithm="sha256",
                    content_hash=page_hash,
                    byte_size=len(identifier.encode()),
                )
            )
            version = DocumentVersionRepository(session).register(
                document=document,
                raw_artifact=raw_artifact,
                media_type="text/html",
            )
            session.commit()
            return version.id


if __name__ == "__main__":
    unittest.main()
