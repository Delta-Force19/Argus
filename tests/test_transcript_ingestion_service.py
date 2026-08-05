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
            artifact.payload["text"], "First story. Second story."
        )
        self.assertEqual(artifact.method_version, "5")
        self.assertEqual(
            artifact.payload["normalization"],
            {
                "strategy": "timing-aware-caption-rollup",
                "cue_count": 2,
                "removed_overlap_word_count": 0,
            },
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

    def test_collapses_exact_overlap_from_rolling_webvtt_cues(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:04.000\n"
            b"At least 25 people were killed by\n\n"
            b"00:00:02.000 --> 00:00:06.000\n"
            b"At least 25 people were killed by Israeli air strikes\n\n"
            b"00:00:04.000 --> 00:00:08.000\n"
            b"Israeli air strikes and gunshots overnight.\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "At least 25 people were killed by Israeli air strikes and "
            "gunshots overnight.",
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            10,
        )
        self.assertNotIn("\n", artifact.payload["text"])

    def test_preserves_repetition_across_non_overlapping_cues(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nNever again.\n\n"
            b"00:00:01.000 --> 00:00:02.000\nNever again.\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"], "Never again. Never again."
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            0,
        )

    def test_collapses_timestamped_rollup_at_touching_boundaries(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:02.000\n"
            b"At least 25 people were<00:00:01.500><c> killed</c>\n\n"
            b"00:00:02.010 --> 00:00:04.000\n"
            b"At least 25 people were killed"
            b"<00:00:02.400><c> overnight.</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "At least 25 people were killed overnight.",
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            6,
        )

    def test_collapses_youtube_duplicate_display_line_inside_cue(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:02.000\n"
            b"At least 25 people were killed by\n"
            b"At least 25 people were killed by"
            b"<00:00:01.500><c> Israeli air strikes and gunshots</c>\n\n"
            b"00:00:02.010 --> 00:00:04.000\n"
            b"Israeli air strikes and gunshots\n"
            b"Israeli air strikes and gunshots"
            b"<00:00:02.400><c> overnight.</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "At least 25 people were killed by Israeli air strikes and "
            "gunshots overnight.",
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            17,
        )

    def test_collapses_youtube_short_relay_cues(self) -> None:
        content = (
            b"WEBVTT\nKind: captions\nLanguage: en\n\n"
            b"00:00:00.400 --> 00:00:03.030 align:start position:0%\n"
            b"At<00:00:00.560><c> least</c><00:00:01.040><c> 25</c>"
            b"<00:00:01.680><c> people</c><00:00:02.080><c> were</c>"
            b"<00:00:02.399><c> killed</c><00:00:02.800><c> by</c>\n\n"
            b"00:00:03.030 --> 00:00:03.040 align:start position:0%\n"
            b"At least 25 people were killed by\n\n"
            b"00:00:03.040 --> 00:00:05.030 align:start position:0%\n"
            b"At least 25 people were killed by\n"
            b"Israeli<00:00:03.600><c> air</c><00:00:03.840><c> strikes</c>"
            b"<00:00:04.240><c> and</c><00:00:04.480><c> gunshots</c>\n\n"
            b"00:00:05.030 --> 00:00:05.040 align:start position:0%\n"
            b"Israeli air strikes and gunshots\n\n"
            b"00:00:05.040 --> 00:00:07.430 align:start position:0%\n"
            b"Israeli air strikes and gunshots\n"
            b"overnight,<00:00:05.759><c> according</c><00:00:06.160>"
            b"<c> to</c><00:00:06.480><c> health</c><00:00:06.799>"
            b"<c> officials</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "At least 25 people were killed by Israeli air strikes and "
            "gunshots overnight, according to health officials",
        )
        self.assertEqual(
            artifact.payload["normalization"]["cue_count"], 5
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            24,
        )

    def test_preserves_short_cue_without_three_cue_relay_evidence(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nNever again.\n\n"
            b"00:00:01.000 --> 00:00:01.010\nNever again.\n\n"
            b"00:00:01.010 --> 00:00:02.000\n"
            b"<00:00:01.010><c>Never again.</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "Never again. Never again. Never again.",
        )

    def test_collapses_youtube_relay_before_untimed_final_cue(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:18.800 --> 00:00:21.429 align:start position:0%\n"
            b"hospital, most of the victims were\n"
            b"killed<00:00:19.119><c> by</c><00:00:19.439><c> gunfire</c>"
            b"<00:00:20.160><c> as</c><00:00:20.400><c> they</c>"
            b"<00:00:20.640><c> waited</c><00:00:20.960><c> for</c>"
            b"<00:00:21.119><c> aid</c>\n\n"
            b"00:00:21.429 --> 00:00:21.439 align:start position:0%\n"
            b"killed by gunfire as they waited for aid\n\n"
            b"00:00:21.439 --> 00:00:23.429 align:start position:0%\n"
            b"killed by gunfire as they waited for aid\n"
            b"trucks<00:00:21.840><c> close</c><00:00:22.080><c> to</c>"
            b"<00:00:22.320><c> Zikim</c><00:00:22.880><c> crossing</c>"
            b"<00:00:23.279><c> with</c>\n\n"
            b"00:00:23.429 --> 00:00:23.439 align:start position:0%\n"
            b"trucks close to Zikim crossing with\n\n"
            b"00:00:23.439 --> 00:00:42.630 align:start position:0%\n"
            b"trucks close to Zikim crossing with\nIsrael.\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "hospital, most of the victims were killed by gunfire as they "
            "waited for aid trucks close to Zikim crossing with Israel.",
        )

    def test_preserves_untimed_repeated_visual_lines(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:02.000\n"
            b"Never again.\nNever again.\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"], "Never again. Never again."
        )

    def test_preserves_new_timestamped_repetition_at_touching_boundary(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\n"
            b"Never again.\n\n"
            b"00:00:01.000 --> 00:00:02.000\n"
            b"<00:00:01.000><c>Never again.</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"], "Never again. Never again."
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            0,
        )

    def test_preserves_rollup_prefix_after_material_timing_gap(self) -> None:
        content = (
            b"WEBVTT\n\n"
            b"00:00:00.000 --> 00:00:01.000\nNever again.\n\n"
            b"00:00:01.200 --> 00:00:02.000\n"
            b"Never again.<00:00:01.500><c> Today.</c>\n"
        )

        result = self._ingest(content)

        artifact = self.session.get(
            DerivedArtifact, result.transcript_artifact_id
        )
        self.assertEqual(
            artifact.payload["text"],
            "Never again. Never again. Today.",
        )
        self.assertEqual(
            artifact.payload["normalization"]["removed_overlap_word_count"],
            0,
        )

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
