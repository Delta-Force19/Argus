import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

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
    EntityCandidate,
    EntityResolutionEvidence,
)
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.entity_registry_audit_service import (
    EntityResolutionValidity,
    get_entity_registry_audit,
)
from argus.services.entity_resolution_service import (
    EntityResolutionService,
)


class EntityRegistryAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.left = self._candidate("un")
        self.right = self._candidate("united nations")
        self.proposal = self._proposal(self.left, self.right)
        self.approval = self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self.entity = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=self.proposal.id,
            canonical_candidate_id=self.right.id,
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_current_consumed_approval_is_active_and_safe(self) -> None:
        report = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(report.entity_count, 1)
        self.assertEqual(report.safe_entity_count, 1)
        self.assertEqual(report.blocked_entity_count, 0)
        self.assertEqual(report.link_count, 1)
        self.assertEqual(
            report.counts_by_validity[0].validity,
            EntityResolutionValidity.ACTIVE,
        )
        item = report.items[0]
        self.assertTrue(item.safe_for_downstream_use)
        self.assertEqual(item.applied_decision_ids, (self.approval.id,))
        self.assertEqual(item.latest_decision_id, self.approval.id)
        self.assertEqual(
            item.validity,
            EntityResolutionValidity.ACTIVE,
        )

    def test_new_approval_requires_explicit_reapplication(self) -> None:
        second = self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self.session.commit()

        pending = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(pending.safe_entity_count, 0)
        self.assertEqual(
            pending.items[0].validity,
            EntityResolutionValidity.PENDING_REAPPLICATION,
        )
        self.assertEqual(pending.items[0].latest_decision_id, second.id)

        EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=self.proposal.id,
        )
        self.session.commit()
        active = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(active.safe_entity_count, 1)
        self.assertEqual(
            active.items[0].applied_decision_ids,
            (self.approval.id, second.id),
        )
        self.assertEqual(
            active.items[0].validity,
            EntityResolutionValidity.ACTIVE,
        )

    def test_rejection_revokes_applied_registry_link(self) -> None:
        rejection = self._decide(
            self.proposal,
            AliasDecisionStatus.REJECTED,
        )
        self.session.commit()

        report = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        item = report.items[0]
        self.assertFalse(item.safe_for_downstream_use)
        self.assertEqual(item.latest_decision_id, rejection.id)
        self.assertEqual(item.latest_status, AliasDecisionStatus.REJECTED)
        self.assertEqual(
            item.validity,
            EntityResolutionValidity.REVOKED,
        )

    def test_needs_review_suspends_applied_registry_link(self) -> None:
        review = self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.session.commit()

        report = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        item = report.items[0]
        self.assertEqual(item.latest_decision_id, review.id)
        self.assertEqual(
            item.validity,
            EntityResolutionValidity.NEEDS_REVIEW,
        )
        self.assertEqual(report.blocked_entity_count, 1)

    def test_one_invalid_extension_blocks_whole_entity(self) -> None:
        third = self._candidate("un organisation")
        extension = self._proposal(self.left, third)
        self._decide(extension, AliasDecisionStatus.APPROVED)
        EntityResolutionService(
            self.session
        ).resolve_approved_alias(proposal_id=extension.id)
        self._decide(extension, AliasDecisionStatus.REJECTED)
        self.session.commit()

        report = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(report.link_count, 2)
        self.assertEqual(report.safe_entity_count, 0)
        self.assertTrue(
            all(
                not item.safe_for_downstream_use
                for item in report.items
            )
        )
        self.assertEqual(
            {item.validity for item in report.items},
            {
                EntityResolutionValidity.ACTIVE,
                EntityResolutionValidity.REVOKED,
            },
        )

    def test_audit_is_read_only_and_validates_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit"):
            get_entity_registry_audit(
                limit=0,
                session_factory=self.session_factory,
            )

        before = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )
        get_entity_registry_audit(
            session_factory=self.session_factory,
        )
        self.session.expire_all()
        after = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )

        self.assertEqual(after, before)

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
