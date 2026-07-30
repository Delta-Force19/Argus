import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from argus.database import Base
from argus.knowledge import (
    AliasDecisionStatus,
    AliasSignalType,
    EntityType,
    ManualAliasDecision,
)
from argus.models import (
    AliasDecision,
    AliasProposal,
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityResolutionEvidence,
)
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.entity_resolution_service import (
    EntityResolutionService,
    resolve_alias_identity,
)


class EntityResolutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.left = self._candidate("un")
        self.right = self._candidate("united nations")
        self.proposal = self._proposal(self.left, self.right)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_creates_entity_only_from_latest_approved_decision(self) -> None:
        decision = self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )

        result = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )

        self.assertTrue(result.entity_created)
        self.assertEqual(result.canonical_name, "united nations")
        self.assertEqual(result.alias_decision_id, decision.id)
        self.assertEqual(
            result.assigned_candidate_ids,
            (self.left.id, self.right.id),
        )
        entity = self.session.get(Entity, result.entity_id)
        self.assertEqual(entity.entity_type, EntityType.ORGANIZATION)
        assignments = list(
            self.session.scalars(
                select(EntityCandidateAssignment).order_by(
                    EntityCandidateAssignment.entity_candidate_id
                )
            )
        )
        self.assertEqual(len(assignments), 2)
        self.assertTrue(
            all(item.entity_id == entity.id for item in assignments)
        )
        evidence = self.session.scalar(
            select(EntityResolutionEvidence)
        )
        self.assertEqual(evidence.alias_decision_id, decision.id)

    def test_rejects_missing_nonfinal_or_superseded_approval(self) -> None:
        service = EntityResolutionService(self.session)
        with self.assertRaisesRegex(ValueError, "latest.*approved"):
            service.resolve_approved_alias(
                proposal_id=self.proposal.id,
                canonical_candidate_id=self.right.id,
            )

        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        with self.assertRaisesRegex(ValueError, "latest.*approved"):
            service.resolve_approved_alias(
                proposal_id=self.proposal.id,
                canonical_candidate_id=self.right.id,
            )

    def test_requires_explicit_canonical_candidate_for_new_entity(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        service = EntityResolutionService(self.session)

        with self.assertRaisesRegex(
                ValueError,
                "requires canonical_candidate_id",
        ):
            service.resolve_approved_alias(
                proposal_id=self.proposal.id,
            )
        with self.assertRaisesRegex(ValueError, "belong"):
            service.resolve_approved_alias(
                proposal_id=self.proposal.id,
                canonical_candidate_id=999,
            )

    def test_extends_inferred_entity_without_changing_canonical_name(
            self,
    ) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        first = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )
        third = self._candidate("un organisation")
        extension = self._proposal(self.left, third)
        extension_decision = self._decide(
            extension,
            AliasDecisionStatus.APPROVED,
        )

        result = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=extension.id,
        )

        self.assertFalse(result.entity_created)
        self.assertEqual(result.entity_id, first.entity_id)
        self.assertEqual(result.canonical_name, "united nations")
        self.assertEqual(result.alias_decision_id, extension_decision.id)
        assignment = self.session.scalar(
            select(EntityCandidateAssignment).where(
                EntityCandidateAssignment.entity_candidate_id == third.id
            )
        )
        self.assertEqual(assignment.entity_id, first.entity_id)

    def test_refuses_implicit_merge_of_two_existing_entities(self) -> None:
        fourth = self._candidate("icc")
        fifth = self._candidate("international criminal court")
        second_proposal = self._proposal(fourth, fifth)
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self._decide(
            second_proposal,
            AliasDecisionStatus.APPROVED,
        )
        service = EntityResolutionService(self.session)
        service.resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )
        service.resolve_approved_alias(
            proposal_id=second_proposal.id,
            canonical_candidate_id=fifth.id,
        )
        merge_proposal = self._proposal(self.left, fourth)
        self._decide(
            merge_proposal,
            AliasDecisionStatus.APPROVED,
        )

        with self.assertRaisesRegex(ValueError, "explicit entity merge"):
            service.resolve_approved_alias(
                proposal_id=merge_proposal.id,
            )

    def test_same_approved_decision_is_idempotent(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        service = EntityResolutionService(self.session)
        first = service.resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )
        second = service.resolve_approved_alias(
            proposal_id=self.proposal.id,
        )

        self.assertEqual(second.entity_id, first.entity_id)
        self.assertFalse(second.entity_created)
        with self.assertRaisesRegex(ValueError, "canonical candidate"):
            service.resolve_approved_alias(
                proposal_id=self.proposal.id,
                canonical_candidate_id=self.left.id,
            )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Entity)
            ),
            1,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    EntityResolutionEvidence
                )
            ),
            1,
        )

    def test_service_does_not_commit(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self.session.commit()
        EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Entity)
            ),
            0,
        )

    def test_application_boundary_commits_detached_result(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self.session.commit()
        proposal_id = self.proposal.id
        canonical_candidate_id = self.right.id
        self.session.close()

        result = resolve_alias_identity(
            proposal_id=proposal_id,
            canonical_candidate_id=canonical_candidate_id,
            session_factory=self.session_factory,
        )

        self.assertTrue(result.entity_created)
        with self.session_factory() as session:
            self.assertIsNotNone(session.get(Entity, result.entity_id))
        self.session = self.session_factory()

    def _candidate(self, text: str) -> EntityCandidate:
        next_id = (
            self.session.scalar(
                select(func.count()).select_from(EntityCandidate)
            )
            or 0
        ) + 1
        row = EntityCandidate(
            derived_artifact_id=next_id,
            entity_mention_id=next_id,
            document_version_id=1,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=text,
            context_text=f"{text} appeared in the source.",
            context_start_char=0,
            context_end_char=len(text),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _proposal(
            self,
            first: EntityCandidate,
            second: EntityCandidate,
    ) -> AliasProposal:
        left, right = sorted((first, second), key=lambda item: item.id)
        row = AliasProposal(
            derived_artifact_id=left.id * 100 + right.id,
            document_version_id=1,
            left_entity_candidate_id=left.id,
            right_entity_candidate_id=right.id,
            entity_type=EntityType.ORGANIZATION,
            left_canonical_text=left.canonical_text,
            right_canonical_text=right.canonical_text,
            signal_type=AliasSignalType.ACRONYM,
            confidence_score=0.80,
            confidence_basis="deterministic-heuristic-v1",
            rationale="Reviewed alias signal.",
            left_occurrence_count=1,
            right_occurrence_count=1,
            shared_document_count=1,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _decide(
            self,
            proposal: AliasProposal,
            status: AliasDecisionStatus,
    ) -> AliasDecision:
        return AliasDecisionService(self.session).decide(
            proposal_id=proposal.id,
            decision=ManualAliasDecision(
                status=status,
                reason="Manual evidence review.",
                reviewer="analyst",
            ),
        )
