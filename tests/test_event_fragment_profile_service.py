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
from argus.models import DerivedArtifact
from argus.services.event_fragment_profile_service import (
    profile_event_fragments,
)
from argus.services.event_fragment_service import (
    register_event_fragment_candidate,
)
from argus.services.event_observation_extraction_service import (
    extract_event_observations,
)
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventFragmentProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.test/profile",
            document_type=DocumentType.ARTICLE,
            language="en",
        )
        raw = RawArtifactRepository(self.session).get_or_create(StoredArtifact(
            storage_backend="filesystem",
            storage_key="sha256/test/" + "b" * 64,
            hash_algorithm="sha256",
            content_hash=hashlib.sha256(b"profile-source").hexdigest(),
            byte_size=64,
        ))
        self.version = DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=raw,
        )
        self.text = "Gaza was attacked and officials said it."
        text_artifact = DerivedArtifactRepository(self.session).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.TRANSCRIPT,
            method="test-transcript",
            method_version="1",
            schema_version="1",
            payload={"text": self.text, "character_count": len(self.text)},
        )
        register_event_fragment_candidate(
            self.session,
            document_version_id=self.version.id,
            text_derived_artifact_id=text_artifact.id,
            start_char=0,
            end_char=len(self.text),
            method="manual",
            method_version="1",
            created_by="test",
            rationale="One test fragment.",
        )
        self.session.commit()
        observations = extract_event_observations(
            document_version_id=self.version.id,
            text_derived_artifact_id=text_artifact.id,
            persist=True,
            extractor=_FakeExtractor(),
            session_factory=lambda: Session(self.engine),
        )
        self.observation_artifact_id = observations.event_observation_artifact_id

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_preview_accounts_for_every_observation_without_writes(self) -> None:
        report = self._profile(persist=False)

        self.assertFalse(report.persisted)
        self.assertIsNone(report.event_fragment_profile_artifact_id)
        self.assertEqual(report.raw_observation_count, 4)
        self.assertEqual(report.retained_occurrence_count, 2)
        self.assertEqual(report.signal_count, 2)
        self.assertEqual(report.exclusion_count, 2)
        with Session(self.engine) as session:
            count = session.scalar(
                select(func.count()).select_from(DerivedArtifact).where(
                    DerivedArtifact.artifact_type
                    == DerivedArtifactType.EVENT_FRAGMENT_PROFILES
                )
            )
        self.assertEqual(count, 0)

    def test_persistence_is_content_addressed_and_idempotent(self) -> None:
        first = self._profile(persist=True)
        second = self._profile(persist=True)

        self.assertEqual(
            first.event_fragment_profile_artifact_id,
            second.event_fragment_profile_artifact_id,
        )
        with Session(self.engine) as session:
            artifact = session.get(
                DerivedArtifact,
                first.event_fragment_profile_artifact_id,
            )
        self.assertEqual(
            artifact.artifact_type,
            DerivedArtifactType.EVENT_FRAGMENT_PROFILES,
        )
        self.assertEqual(artifact.method_version, "2")
        self.assertEqual(
            artifact.payload["event_observation_artifact_id"],
            self.observation_artifact_id,
        )
        self.assertEqual(len(artifact.payload["profiles"]), 1)
        self.assertEqual(len(artifact.payload["profiles"][0]["exclusions"]), 2)

    def test_rejects_projection_that_disagrees_with_immutable_payload(self) -> None:
        with Session(self.engine) as session:
            artifact = session.get(
                DerivedArtifact,
                self.observation_artifact_id,
            )
            payload = dict(artifact.payload)
            observations = [dict(item) for item in payload["observations"]]
            observations[0]["normalized_value"] = "tampered"
            payload["observations"] = observations
            artifact.payload = payload
            session.commit()

        with self.assertRaisesRegex(ValueError, "projected rows disagree"):
            self._profile(persist=False)

    def test_rejects_missing_document_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            profile_event_fragments(
                document_version_id=self.version.id + 100,
                event_observation_artifact_id=self.observation_artifact_id,
                session_factory=lambda: Session(self.engine),
            )

    def _profile(self, *, persist: bool):
        return profile_event_fragments(
            document_version_id=self.version.id,
            event_observation_artifact_id=self.observation_artifact_id,
            persist=persist,
            session_factory=lambda: Session(self.engine),
        )


class _FakeExtractor:
    method = "fake-event-observations"

    def method_version(self, language: str) -> str:
        return f"fake-{language}@1"

    def extract(self, text: str, *, language: str):
        del language
        definitions = (
            (EventObservationType.PLACE_MENTION, "GPE", "Gaza", "gaza"),
            (EventObservationType.ACTION_CANDIDATE, "VERB:ROOT", "attacked", "attack"),
            (EventObservationType.ACTION_CANDIDATE, "VERB:conj", "said", "say"),
            (EventObservationType.OBJECT_CANDIDATE, "PRON:obj", "it", "it"),
        )
        return EventObservationExtractionResult(
            observations=tuple(
                ExtractedEventObservation(
                    observation_type=observation_type,
                    source_label=label,
                    surface_text=surface,
                    normalized_value=value,
                    start_char=text.index(surface),
                    end_char=text.index(surface) + len(surface),
                    rationale="Test observation.",
                )
                for observation_type, label, surface, value in definitions
            ),
        )


if __name__ == "__main__":
    unittest.main()
