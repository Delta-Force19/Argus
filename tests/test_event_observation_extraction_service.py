import hashlib
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.event_observations import (
    EventObservationExtractionResult,
    EventObservationType,
    ExtractedEventObservation,
)
from argus.models import DerivedArtifact, EventObservationCandidate
from argus.services.event_fragment_service import (
    register_event_fragment_candidate,
)
from argus.services.event_observation_extraction_service import (
    extract_event_observations,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventObservationExtractionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.test/bulletin",
            document_type=DocumentType.ARTICLE,
            language="en",
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/test/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash=hashlib.sha256(b"source").hexdigest(),
                byte_size=128,
            )
        )
        self.version = DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=raw,
        )
        self.text = "Cyprus contained fires. Airports changed scanners."
        self.artifact = DerivedArtifactRepository(self.session).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.TRANSCRIPT,
            method="test-transcript",
            method_version="1",
            schema_version="1",
            payload={
                "text": self.text,
                "character_count": len(self.text),
            },
        )
        split = self.text.index("Airports") - 1
        self.first = register_event_fragment_candidate(
            self.session,
            document_version_id=self.version.id,
            text_derived_artifact_id=self.artifact.id,
            start_char=0,
            end_char=split,
            method="manual",
            method_version="1",
            created_by="test",
            rationale="First story.",
            quality_limitations=("Manual test boundary.",),
        )
        self.second = register_event_fragment_candidate(
            self.session,
            document_version_id=self.version.id,
            text_derived_artifact_id=self.artifact.id,
            start_char=split + 1,
            end_char=len(self.text),
            method="manual",
            method_version="1",
            created_by="test",
            rationale="Second story.",
            quality_limitations=("Manual test boundary.",),
        )
        self.session.commit()
        self.extractor = _FakeExtractor()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_preview_preserves_absolute_source_offsets_without_writes(
            self,
    ) -> None:
        report = self._extract(persist=False)

        self.assertFalse(report.persisted)
        self.assertIsNone(report.event_observation_artifact_id)
        self.assertEqual(report.fragment_count, 2)
        self.assertEqual(report.observation_count, 4)
        self.assertEqual(
            tuple(item.event_fragment_id for item in report.items),
            (
                self.first.event_fragment_id,
                self.first.event_fragment_id,
                self.second.event_fragment_id,
                self.second.event_fragment_id,
            ),
        )
        self.assertEqual(report.items[0].surface_text, "Cyprus")
        self.assertEqual(report.items[0].start_char, 0)
        self.assertEqual(
            self.text[
                report.items[-1].start_char:report.items[-1].end_char
            ],
            report.items[-1].surface_text,
        )
        with Session(self.engine) as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(
                        EventObservationCandidate
                    )
                ),
                0,
            )

    def test_persistence_is_idempotent_and_content_addressed(self) -> None:
        first = self._extract(persist=True)
        second = self._extract(persist=True)

        self.assertEqual(
            first.event_observation_artifact_id,
            second.event_observation_artifact_id,
        )
        self.assertEqual(
            tuple(item.event_observation_id for item in first.items),
            tuple(item.event_observation_id for item in second.items),
        )
        with Session(self.engine) as session:
            artifact = session.get(
                DerivedArtifact,
                first.event_observation_artifact_id,
            )
            self.assertEqual(
                artifact.artifact_type,
                DerivedArtifactType.EVENT_OBSERVATIONS,
            )
            self.assertEqual(artifact.payload["text_artifact_id"], self.artifact.id)
            self.assertEqual(len(artifact.payload["observations"]), 4)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(
                        EventObservationCandidate
                    )
                ),
                4,
            )

    def test_rejects_ambiguous_fragment_methods(self) -> None:
        register_event_fragment_candidate(
            self.session,
            document_version_id=self.version.id,
            text_derived_artifact_id=self.artifact.id,
            start_char=0,
            end_char=len(self.text),
            method="alternative",
            method_version="2",
            created_by="test",
            rationale="Alternative span.",
        )
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "Several fragment methods"):
            self._extract(persist=False)

    def test_rejects_extractor_offsets_that_do_not_match_source(self) -> None:
        extractor = _FakeExtractor(surface_override="Wrong")

        with self.assertRaisesRegex(ValueError, "does not match"):
            self._extract(persist=False, extractor=extractor)

    def _extract(self, *, persist: bool, extractor=None):
        return extract_event_observations(
            document_version_id=self.version.id,
            text_derived_artifact_id=self.artifact.id,
            persist=persist,
            extractor=extractor or self.extractor,
            session_factory=lambda: Session(self.engine),
        )


class _FakeExtractor:
    method = "fake-event-observations"

    def __init__(self, *, surface_override: str | None = None) -> None:
        self.surface_override = surface_override

    def method_version(self, language: str) -> str:
        return f"fake-{language}@1"

    def extract(
            self,
            text: str,
            *,
            language: str,
    ) -> EventObservationExtractionResult:
        del language
        first_word = text.split()[0]
        action = "contained" if "contained" in text else "changed"
        action_start = text.index(action)
        return EventObservationExtractionResult(
            observations=(
                ExtractedEventObservation(
                    observation_type=(
                        EventObservationType.PLACE_MENTION
                        if first_word == "Cyprus"
                        else EventObservationType.OBJECT_CANDIDATE
                    ),
                    source_label="TEST",
                    surface_text=self.surface_override or first_word,
                    normalized_value=first_word.casefold(),
                    start_char=0,
                    end_char=len(first_word),
                    rationale="Deterministic test signal.",
                ),
                ExtractedEventObservation(
                    observation_type=EventObservationType.ACTION_CANDIDATE,
                    source_label="VERB:ROOT",
                    surface_text=action,
                    normalized_value=action,
                    start_char=action_start,
                    end_char=action_start + len(action),
                    rationale="Deterministic test action.",
                ),
            ),
            quality_limitations=("Test extractor limitation.",),
        )


if __name__ == "__main__":
    unittest.main()
