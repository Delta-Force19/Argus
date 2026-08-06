import hashlib
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.models import DerivedArtifact
from argus.services.event_fragment_cluster_proposal_service import (
    propose_event_fragment_clusters,
)
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


def raw_pair(left, right, status):
    candidate = status == "candidate"
    matches = [{
        "observation_type": "place_mention",
        "normalized_value": "shared place",
        "left_observation_ids": [left * 10 + 1],
        "right_observation_ids": [right * 10 + 1],
        "evidence_points": 3,
        "rationale": "Exact place.",
    }, {
        "observation_type": "action_candidate",
        "normalized_value": "shared action",
        "left_observation_ids": [left * 10 + 2],
        "right_observation_ids": [right * 10 + 2],
        "evidence_points": 2,
        "rationale": "Exact action.",
    }] if candidate else []
    return {
        "left_event_fragment_id": left,
        "right_event_fragment_id": right,
        "status": status,
        "evidence_dimensions": (
            ["action_candidate", "place_mention"] if candidate else []
        ),
        "evidence_points": 5 if candidate else 0,
        "rationale": f"{status} pair.",
        "matches": matches,
    }


class EventFragmentClusterProposalServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value="https://example.test/cluster-proposals",
                document_type=DocumentType.ARTICLE,
                language="en",
            )
            raw = RawArtifactRepository(session).get_or_create(StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/test/" + "b" * 64,
                hash_algorithm="sha256",
                content_hash=hashlib.sha256(b"cluster-source").hexdigest(),
                byte_size=14,
            ))
            version = DocumentVersionRepository(session).register(
                document=document,
                raw_artifact=raw,
            )
            artifact = DerivedArtifactRepository(session).register(
                document_version=version,
                artifact_type=DerivedArtifactType.EVENT_FRAGMENT_PAIR_CANDIDATES,
                method="test-pairs",
                method_version="1",
                schema_version="1",
                payload={"pairs": [
                    raw_pair(1, 2, "weak"),
                    raw_pair(1, 3, "candidate"),
                    raw_pair(2, 3, "candidate"),
                ]},
                quality_limitations=("Pair limitation.",),
            )
            session.commit()
            self.version_id = version.id
            self.artifact_id = artifact.id

    def tearDown(self):
        self.engine.dispose()

    def test_preview_is_read_only_and_persist_is_idempotent(self):
        preview = self._propose(False)
        first = self._propose(True)
        second = self._propose(True)

        self.assertIsNone(preview.cluster_proposal_artifact_id)
        self.assertEqual(
            first.cluster_proposal_artifact_id,
            second.cluster_proposal_artifact_id,
        )
        self.assertEqual(
            [item.event_fragment_ids for item in first.proposals],
            [(1, 3), (2, 3)],
        )
        with Session(self.engine) as session:
            artifact = session.get(
                DerivedArtifact, first.cluster_proposal_artifact_id
            )
        self.assertEqual(
            artifact.artifact_type,
            DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_PROPOSALS,
        )
        self.assertEqual(
            artifact.payload["fragment_pair_candidate_artifact_id"],
            self.artifact_id,
        )
        self.assertIn("Pair limitation.", artifact.quality_limitations)

    def test_rejects_inconsistent_payload(self):
        with Session(self.engine) as session:
            artifact = session.get(DerivedArtifact, self.artifact_id)
            artifact.payload = {"pairs": [{"status": "candidate"}]}
            session.commit()
        with self.assertRaisesRegex(ValueError, "payload is inconsistent"):
            self._propose(False)

    def _propose(self, persist):
        return propose_event_fragment_clusters(
            document_version_id=self.version_id,
            fragment_pair_candidate_artifact_id=self.artifact_id,
            persist=persist,
            session_factory=lambda: Session(self.engine),
        )


if __name__ == "__main__":
    unittest.main()
