import hashlib
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.models import DerivedArtifact
from argus.services.event_fragment_pair_candidate_service import (
    compare_event_fragment_profiles,
)
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.document_repository import DocumentRepository, DocumentVersionRepository
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventFragmentPairCandidateServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value="https://example.test/pairs",
                document_type=DocumentType.ARTICLE,
                language="en",
            )
            raw = RawArtifactRepository(session).get_or_create(StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/test/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash=hashlib.sha256(b"pair-source").hexdigest(),
                byte_size=11,
            ))
            version = DocumentVersionRepository(session).register(
                document=document,
                raw_artifact=raw,
            )
            artifact = DerivedArtifactRepository(session).register(
                document_version=version,
                artifact_type=DerivedArtifactType.EVENT_FRAGMENT_PROFILES,
                method="test-profiles",
                method_version="2",
                schema_version="1",
                payload={"profiles": [
                    {"event_fragment_id": 1, "signals": [
                        {"observation_type": "place_mention", "normalized_value": "gaza", "observation_ids": [1]},
                        {"observation_type": "action_candidate", "normalized_value": "attack", "observation_ids": [2]},
                    ], "exclusions": []},
                    {"event_fragment_id": 2, "signals": [
                        {"observation_type": "place_mention", "normalized_value": "gaza", "observation_ids": [3]},
                        {"observation_type": "action_candidate", "normalized_value": "attack", "observation_ids": [4]},
                    ], "exclusions": []},
                ]},
                quality_limitations=("Profile limitation.",),
            )
            session.commit()
            self.version_id = version.id
            self.artifact_id = artifact.id

    def tearDown(self):
        self.engine.dispose()

    def test_preview_is_read_only_and_persist_is_idempotent(self):
        preview = self._compare(False)
        first = self._compare(True)
        second = self._compare(True)

        self.assertIsNone(preview.fragment_pair_candidate_artifact_id)
        self.assertEqual(
            first.fragment_pair_candidate_artifact_id,
            second.fragment_pair_candidate_artifact_id,
        )
        self.assertEqual(first.pairs[0].status.value, "candidate")
        with Session(self.engine) as session:
            artifact = session.get(
                DerivedArtifact,
                first.fragment_pair_candidate_artifact_id,
            )
        self.assertEqual(
            artifact.artifact_type,
            DerivedArtifactType.EVENT_FRAGMENT_PAIR_CANDIDATES,
        )
        self.assertEqual(
            artifact.payload["event_fragment_profile_artifact_id"],
            self.artifact_id,
        )

    def _compare(self, persist):
        return compare_event_fragment_profiles(
            document_version_id=self.version_id,
            event_fragment_profile_artifact_id=self.artifact_id,
            persist=persist,
            session_factory=lambda: Session(self.engine),
        )


if __name__ == "__main__":
    unittest.main()
