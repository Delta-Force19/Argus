import hashlib
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.event_fragment_cluster_reviews import ComponentReviewStatus
from argus.models import DerivedArtifact
from argus.services.event_fragment_cluster_review_service import (
    review_event_fragment_clusters,
)
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.document_repository import DocumentRepository, DocumentVersionRepository
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EventFragmentClusterReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            document = DocumentRepository(session).get_or_create(
                identifier_scheme="uri",
                identifier_value="https://example.test/cluster-review",
                document_type=DocumentType.ARTICLE,
                language="en",
            )
            raw = RawArtifactRepository(session).get_or_create(StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/test/" + "c" * 64,
                hash_algorithm="sha256",
                content_hash=hashlib.sha256(b"review-source").hexdigest(),
                byte_size=13,
            ))
            version = DocumentVersionRepository(session).register(
                document=document, raw_artifact=raw,
            )
            artifact = DerivedArtifactRepository(session).register(
                document_version=version,
                artifact_type=DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_PROPOSALS,
                method="test-proposals", method_version="1", schema_version="1",
                payload={
                    "proposals": [
                        {"proposal_id": 1, "event_fragment_ids": [1, 3]},
                        {"proposal_id": 2, "event_fragment_ids": [2, 3]},
                    ],
                    "components": [
                        {"event_fragment_ids": [1, 2, 3], "proposal_ids": [1, 2], "status": "ambiguous"},
                        {"event_fragment_ids": [4], "proposal_ids": [], "status": "isolated"},
                    ],
                },
            )
            session.commit()
            self.version_id = version.id
            self.artifact_id = artifact.id

    def tearDown(self):
        self.engine.dispose()

    def test_preserves_ambiguity_explicitly_and_persists_idempotently(self):
        first = self._review(
            preserved_component_fragment_ids=((1, 2, 3),), persist=True
        )
        second = self._review(
            preserved_component_fragment_ids=((1, 2, 3),), persist=True
        )
        self.assertEqual(
            first.cluster_review_artifact_id,
            second.cluster_review_artifact_id,
        )
        self.assertEqual(
            [item.status for item in first.components],
            [ComponentReviewStatus.PRESERVED_AMBIGUITY, ComponentReviewStatus.ISOLATED],
        )
        with Session(self.engine) as session:
            artifact = session.get(DerivedArtifact, first.cluster_review_artifact_id)
        self.assertEqual(
            artifact.artifact_type,
            DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_REVIEW,
        )
        self.assertEqual(artifact.payload["cluster_proposal_artifact_id"], self.artifact_id)

    def test_resolves_only_with_one_accept_and_all_alternatives_rejected(self):
        report = self._review(
            accepted_proposal_ids=(1,), rejected_proposal_ids=(2,)
        )
        self.assertEqual(report.components[0].status, ComponentReviewStatus.RESOLVED)
        self.assertEqual(report.components[0].accepted_proposal_id, 1)

    def test_rejects_overlapping_acceptances(self):
        with self.assertRaisesRegex(ValueError, "cannot both be accepted"):
            self._review(accepted_proposal_ids=(1, 2))

    def test_rejects_decision_mixed_with_preserved_ambiguity(self):
        with self.assertRaisesRegex(ValueError, "preserved-ambiguity"):
            self._review(
                rejected_proposal_ids=(2,),
                preserved_component_fragment_ids=((1, 2, 3),),
            )

    def _review(self, **kwargs):
        return review_event_fragment_clusters(
            document_version_id=self.version_id,
            cluster_proposal_artifact_id=self.artifact_id,
            reviewer="Victor",
            reason="Evidence does not distinguish the alternatives.",
            session_factory=lambda: Session(self.engine),
            **kwargs,
        )


if __name__ == "__main__":
    unittest.main()
