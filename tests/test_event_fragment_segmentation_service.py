import hashlib
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.models import EventFragmentCandidate
from argus.services.event_fragment_segmentation_service import (
    inspect_document_text,
    segment_event_fragments,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventFragmentSegmentationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.version = self._version("bulletin")
        self.text = (
            "Evening bulletin\n\n"
            "Syria update\n\n"
            "Talks continued.\n\n"
            "Gaza update\n\n"
            "Aid arrived."
        )
        self.artifact = self._artifact(self.version, self.text)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_inspection_exposes_exact_blocks_and_offsets(self) -> None:
        report = inspect_document_text(
            document_version_id=self.version.id,
            session_factory=self._session,
        )

        self.assertEqual(report.text_derived_artifact_id, self.artifact.id)
        self.assertEqual(report.character_count, len(self.text))
        self.assertEqual(report.text_hash, self._hash(self.text))
        self.assertEqual(len(report.blocks), 5)
        self.assertEqual(report.blocks[0].text, "Evening bulletin")
        self.assertEqual(
            self.text[
                report.blocks[3].start_char:report.blocks[3].end_char
            ],
            "Gaza update",
        )
        self.assertTrue(report.blocks[0].heading_candidate)
        self.assertFalse(report.blocks[2].heading_candidate)

    def test_preview_splits_on_repeated_heading_like_blocks(self) -> None:
        report = segment_event_fragments(
            document_version_id=self.version.id,
            session_factory=self._session,
        )

        self.assertFalse(report.persisted)
        self.assertEqual(report.boundary_basis, "heading-like-paragraphs")
        self.assertEqual(report.fragment_count, 2)
        first, second = report.items
        self.assertEqual(self.text[first.start_char:first.end_char], (
            "Evening bulletin\n\nSyria update\n\nTalks continued."
        ))
        self.assertEqual(
            self.text[second.start_char:second.end_char],
            "Gaza update\n\nAid arrived.",
        )
        self.assertIsNone(first.event_fragment_id)
        self.assertEqual(
            self.session.scalar(select(EventFragmentCandidate)),
            None,
        )

    def test_persist_is_explicit_and_idempotent(self) -> None:
        first = segment_event_fragments(
            document_version_id=self.version.id,
            persist=True,
            session_factory=self._session,
        )
        second = segment_event_fragments(
            document_version_id=self.version.id,
            persist=True,
            session_factory=self._session,
        )

        self.assertTrue(first.persisted)
        self.assertEqual(
            tuple(item.event_fragment_id for item in first.items),
            tuple(item.event_fragment_id for item in second.items),
        )
        with self._session() as session:
            self.assertEqual(
                len(session.scalars(select(EventFragmentCandidate)).all()),
                2,
            )

    def test_falls_back_to_whole_non_whitespace_content(self) -> None:
        version = self._version("article")
        text = "  Ordinary first sentence.\n\nOrdinary second sentence.  "
        self._artifact(version, text)
        self.session.commit()

        report = segment_event_fragments(
            document_version_id=version.id,
            session_factory=self._session,
        )

        self.assertEqual(report.boundary_basis, "whole-content-fallback")
        self.assertEqual(report.fragment_count, 1)
        self.assertEqual(
            text[report.items[0].start_char:report.items[0].end_char],
            "Ordinary first sentence.\n\nOrdinary second sentence.",
        )
        self.assertTrue(any(
            "whole non-whitespace text" in value
            for value in report.items[0].quality_limitations
        ))

    def test_rejects_blank_text(self) -> None:
        version = self._version("blank")
        self._artifact(version, " \n\n ")
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "non-whitespace"):
            segment_event_fragments(
                document_version_id=version.id,
                session_factory=self._session,
            )

    def test_requires_explicit_artifact_when_multiple_are_available(self) -> None:
        other = self._artifact(
            self.version,
            self.text,
            artifact_type=DerivedArtifactType.TRANSLATION,
        )
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "multiple supported"):
            inspect_document_text(
                document_version_id=self.version.id,
                session_factory=self._session,
            )
        report = inspect_document_text(
            document_version_id=self.version.id,
            text_derived_artifact_id=other.id,
            session_factory=self._session,
        )
        self.assertEqual(report.text_derived_artifact_id, other.id)

    def test_missing_version_is_not_reported_as_missing_text(self) -> None:
        with self.assertRaisesRegex(
                ValueError,
                "Document version does not exist: 999",
        ):
            inspect_document_text(
                document_version_id=999,
                session_factory=self._session,
            )

    def _session(self) -> Session:
        return Session(self.engine)

    def _version(self, suffix: str):
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value=f"https://example.test/{suffix}",
            document_type=DocumentType.ARTICLE,
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key=f"sha256/{suffix}/" + suffix * 32,
                hash_algorithm="sha256",
                content_hash=self._hash(suffix),
                byte_size=128,
            )
        )
        return DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=raw,
        )

    def _artifact(
            self,
            version,
            text: str,
            *,
            artifact_type: DerivedArtifactType = (
                DerivedArtifactType.EXTRACTED_TEXT
            ),
    ):
        return DerivedArtifactRepository(self.session).register(
            document_version=version,
            artifact_type=artifact_type,
            method="test",
            method_version="1",
            schema_version="1",
            payload={"text": text, "character_count": len(text)},
            quality_limitations=(),
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
