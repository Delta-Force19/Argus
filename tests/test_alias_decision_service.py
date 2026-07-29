import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    AliasDecisionStatus,
    CanonicalizedEntityCandidate,
    EntityType,
    ManualAliasDecision,
    RecognizedEntityMention,
)
from argus.models import AliasDecision
from argus.proposers import DeterministicEntityAliasProposer
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
)
from argus.storage.alias_decision_repository import (
    AliasDecisionRepository,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.entity_candidate_repository import (
    EntityCandidateRepository,
)
from argus.storage.entity_mention_repository import (
    EntityMentionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class AliasDecisionServiceTests(unittest.TestCase):
    TEXT = "UN | United Nations"

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.proposal = self._create_proposal()
        self.session.commit()
        self.service = AliasDecisionService(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_records_first_manual_decision_with_provenance(self) -> None:
        row = self.service.decide(
            proposal_id=self.proposal.id,
            decision=ManualAliasDecision(
                status=AliasDecisionStatus.NEEDS_REVIEW,
                reason="  Ambiguous without case-sensitive context.  ",
                reviewer="  analyst@example  ",
            ),
        )

        self.assertEqual(row.alias_proposal_id, self.proposal.id)
        self.assertEqual(row.revision, 1)
        self.assertIsNone(row.supersedes_alias_decision_id)
        self.assertEqual(row.status, AliasDecisionStatus.NEEDS_REVIEW)
        self.assertEqual(
            row.reason,
            "Ambiguous without case-sensitive context.",
        )
        self.assertEqual(row.reviewer, "analyst@example")

    def test_later_decision_preserves_and_supersedes_history(self) -> None:
        first = self.service.decide(
            proposal_id=self.proposal.id,
            decision=ManualAliasDecision(
                status=AliasDecisionStatus.NEEDS_REVIEW,
                reason="Need more context.",
                reviewer="reviewer-one",
            ),
        )
        second = self.service.decide(
            proposal_id=self.proposal.id,
            decision=ManualAliasDecision(
                status=AliasDecisionStatus.APPROVED,
                reason="Both forms name the UN in the quoted document.",
                reviewer="reviewer-two",
            ),
        )

        self.assertEqual(second.revision, 2)
        self.assertEqual(second.supersedes_alias_decision_id, first.id)
        history = AliasDecisionRepository(
            self.session
        ).get_history(self.proposal.id)
        self.assertEqual([item.id for item in history], [first.id, second.id])
        self.assertEqual(
            [item.status for item in history],
            [
                AliasDecisionStatus.NEEDS_REVIEW,
                AliasDecisionStatus.APPROVED,
            ],
        )
        self.assertEqual(history[0].reason, "Need more context.")

    def test_repository_returns_latest_revision(self) -> None:
        first = self._decide(AliasDecisionStatus.REJECTED, "Too broad.")
        second = self._decide(
            AliasDecisionStatus.NEEDS_REVIEW,
            "New evidence requires review.",
        )

        latest = AliasDecisionRepository(
            self.session
        ).get_latest(self.proposal.id)

        self.assertEqual(latest.id, second.id)
        self.assertNotEqual(latest.id, first.id)

    def test_decision_does_not_commit(self) -> None:
        self._decide(AliasDecisionStatus.APPROVED, "Same organization.")
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(AliasDecision)
            ),
            0,
        )

    def test_rejects_unknown_or_invalid_proposal_id(self) -> None:
        decision = ManualAliasDecision(
            status=AliasDecisionStatus.REJECTED,
            reason="Not the same object.",
            reviewer="reviewer",
        )
        with self.assertRaisesRegex(ValueError, "positive"):
            self.service.decide(proposal_id=0, decision=decision)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.service.decide(proposal_id=9999, decision=decision)

    def test_manual_decision_requires_reason_and_reviewer(self) -> None:
        with self.assertRaisesRegex(ValueError, "status"):
            ManualAliasDecision(
                status="approved",  # type: ignore[arg-type]
                reason="Same organization.",
                reviewer="reviewer",
            )
        with self.assertRaisesRegex(ValueError, "reason"):
            ManualAliasDecision(
                status=AliasDecisionStatus.APPROVED,
                reason=" ",
                reviewer="reviewer",
            )
        with self.assertRaisesRegex(ValueError, "reviewer"):
            ManualAliasDecision(
                status=AliasDecisionStatus.APPROVED,
                reason="Same organization.",
                reviewer=" ",
            )
        with self.assertRaisesRegex(ValueError, "200"):
            ManualAliasDecision(
                status=AliasDecisionStatus.APPROVED,
                reason="Same organization.",
                reviewer="r" * 201,
            )

    def test_status_values_are_stable(self) -> None:
        self.assertEqual(
            [status.value for status in AliasDecisionStatus],
            ["approved", "rejected", "needs_review"],
        )

    def _decide(
            self,
            status: AliasDecisionStatus,
            reason: str,
    ) -> AliasDecision:
        return self.service.decide(
            proposal_id=self.proposal.id,
            decision=ManualAliasDecision(
                status=status,
                reason=reason,
                reviewer="reviewer",
            ),
        )

    def _create_proposal(self):
        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/alias-decision",
            document_type=DocumentType.ARTICLE,
            language="en",
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/ab/" + "a" * 64,
                hash_algorithm="sha256",
                content_hash="a" * 64,
                byte_size=len(self.TEXT),
            )
        )
        version = DocumentVersionRepository(self.session).register(
            document=document,
            raw_artifact=raw,
        )
        text_artifact = DerivedArtifactRepository(self.session).register(
            document_version=version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method="stub-text",
            method_version="1",
            schema_version="1",
            payload={"text": self.TEXT},
        )
        mention_artifact = DerivedArtifactRepository(self.session).register(
            document_version=version,
            artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
            method="stub-ner",
            method_version="stub-en@1",
            schema_version="1",
            payload={
                "input_artifact_id": text_artifact.id,
                "input_content_hash": text_artifact.content_hash,
                "mentions": [],
            },
        )
        mentions = EntityMentionRepository(self.session).register(
            artifact=mention_artifact,
            mentions=(
                RecognizedEntityMention(
                    entity_type=EntityType.ORGANIZATION,
                    source_label="ORG",
                    surface_text="UN",
                    normalized_text="un",
                    start_char=0,
                    end_char=2,
                ),
                RecognizedEntityMention(
                    entity_type=EntityType.ORGANIZATION,
                    source_label="ORG",
                    surface_text="United Nations",
                    normalized_text="united nations",
                    start_char=5,
                    end_char=19,
                ),
            ),
        )
        candidate_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=version,
            artifact_type=DerivedArtifactType.ENTITY_CANDIDATES,
            method="stub-canonicalizer",
            method_version="1",
            schema_version="1",
            payload={
                "input_artifact_id": mention_artifact.id,
                "input_content_hash": mention_artifact.content_hash,
                "decisions": [],
            },
        )
        EntityCandidateRepository(self.session).register(
            artifact=candidate_artifact,
            candidates=tuple(
                CanonicalizedEntityCandidate(
                    entity_mention_id=mention.id,
                    document_version_id=version.id,
                    entity_type=mention.entity_type,
                    canonical_text=mention.normalized_text,
                    context_text=self.TEXT,
                    context_start_char=0,
                    context_end_char=len(self.TEXT),
                )
                for mention in mentions
            ),
        )
        generation = AliasProposalGenerationService(
            self.session,
            proposer=DeterministicEntityAliasProposer(),
        ).generate(candidate_artifact)
        return generation.proposals[0]


if __name__ == "__main__":
    unittest.main()
