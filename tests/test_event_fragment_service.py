import hashlib
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.services.event_fragment_service import (
    get_event_fragments,
    register_event_fragment_candidate,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventFragmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.version = self._version("one")
        self.other_version = self._version("two")
        self.text = "First report. Second report."
        self.artifact = self._artifact(
            self.version,
            text=self.text,
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_registers_source_anchored_candidate(self) -> None:
        item = self._register(start_char=0, end_char=13)

        self.assertIsNotNone(item.event_fragment_id)
        self.assertEqual(item.document_version_id, self.version.id)
        self.assertEqual(item.text_derived_artifact_id, self.artifact.id)
        self.assertEqual(item.start_char, 0)
        self.assertEqual(item.end_char, 13)
        self.assertEqual(
            item.text_hash,
            hashlib.sha256(b"First report.").hexdigest(),
        )
        self.assertEqual(item.quality_limitations, ("Manual boundary.",))

    def test_registration_is_idempotent_for_same_origin(self) -> None:
        first = self._register(start_char=0, end_char=13)
        second = self._register(start_char=0, end_char=13)

        self.assertEqual(first.event_fragment_id, second.event_fragment_id)

    def test_same_origin_rejects_conflicting_provenance(self) -> None:
        self._register(start_char=0, end_char=13)

        with self.assertRaisesRegex(ValueError, "conflicting provenance"):
            self._register(
                start_char=0,
                end_char=13,
                rationale="A different explanation.",
            )

    def test_allows_overlapping_and_alternative_candidates(self) -> None:
        first = self._register(start_char=0, end_char=13)
        overlapping = self._register(start_char=6, end_char=28)
        alternative = self._register(
            start_char=0,
            end_char=13,
            method="deterministic-paragraph-split",
        )

        self.assertEqual(
            len({
                first.event_fragment_id,
                overlapping.event_fragment_id,
                alternative.event_fragment_id,
            }),
            3,
        )

    def test_rejects_span_outside_source_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "inside the text"):
            self._register(start_char=0, end_char=len(self.text) + 1)

    def test_rejects_artifact_from_another_document_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "another document version"):
            register_event_fragment_candidate(
                self.session,
                document_version_id=self.other_version.id,
                text_derived_artifact_id=self.artifact.id,
                start_char=0,
                end_char=13,
                method="manual",
                method_version="1",
                created_by="test",
                rationale="Candidate boundary.",
            )

    def test_rejects_non_text_derived_artifact(self) -> None:
        metadata = self._artifact(
            self.version,
            text=self.text,
            artifact_type=DerivedArtifactType.NORMALIZED_METADATA,
        )

        with self.assertRaisesRegex(ValueError, "supported text"):
            register_event_fragment_candidate(
                self.session,
                document_version_id=self.version.id,
                text_derived_artifact_id=metadata.id,
                start_char=0,
                end_char=13,
                method="manual",
                method_version="1",
                created_by="test",
                rationale="Candidate boundary.",
            )

    def test_rejects_inconsistent_text_payload(self) -> None:
        inconsistent = self._artifact(
            self.other_version,
            text="Broken text",
            character_count=999,
        )

        with self.assertRaisesRegex(ValueError, "payload is inconsistent"):
            register_event_fragment_candidate(
                self.session,
                document_version_id=self.other_version.id,
                text_derived_artifact_id=inconsistent.id,
                start_char=0,
                end_char=6,
                method="manual",
                method_version="1",
                created_by="test",
                rationale="Candidate boundary.",
            )

    def test_reads_candidates_in_source_order_without_event_assignments(
            self,
    ) -> None:
        later = self._register(start_char=14, end_char=28)
        earlier = self._register(start_char=0, end_char=13)
        self.session.commit()

        report = get_event_fragments(
            document_version_id=self.version.id,
            session_factory=lambda: Session(self.engine),
        )

        self.assertEqual(
            tuple(item.event_fragment_id for item in report.items),
            (earlier.event_fragment_id, later.event_fragment_id),
        )
        self.assertEqual(report.event_fragment_count, 2)
        self.assertEqual(report.event_assignment_count, 0)

    def test_reads_fail_closed_for_missing_version_and_tampered_hash(
            self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            get_event_fragments(
                document_version_id=999,
                session_factory=lambda: Session(self.engine),
            )

        item = self._register(start_char=0, end_char=13)
        candidate = self.session.get(
            __import__("argus.models", fromlist=["EventFragmentCandidate"])
            .EventFragmentCandidate,
            item.event_fragment_id,
        )
        candidate.text_hash = "0" * 64
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "hash does not match"):
            get_event_fragments(
                document_version_id=self.version.id,
                session_factory=lambda: Session(self.engine),
            )

    def _register(
            self,
            *,
            start_char: int,
            end_char: int,
            method: str = "manual",
            rationale: str = "Candidate boundary.",
    ):
        return register_event_fragment_candidate(
            self.session,
            document_version_id=self.version.id,
            text_derived_artifact_id=self.artifact.id,
            start_char=start_char,
            end_char=end_char,
            method=method,
            method_version="1",
            created_by="test",
            rationale=rationale,
            quality_limitations=("Manual boundary.",),
        )

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
                content_hash=hashlib.sha256(suffix.encode()).hexdigest(),
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
            *,
            text: str,
            character_count: int | None = None,
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
            payload={
                "text": text,
                "character_count": (
                    len(text) if character_count is None else character_count
                ),
            },
            quality_limitations=(),
        )


if __name__ == "__main__":
    unittest.main()
