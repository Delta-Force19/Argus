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
from argus.services.entity_resolution_service import (
    EntityResolutionService,
)
from argus.services.safe_entity_projection_service import (
    get_safe_entity_projection,
)


class SafeEntityProjectionServiceTests(unittest.TestCase):
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

    def test_projects_complete_active_identity_with_provenance(
            self,
    ) -> None:
        projection = get_safe_entity_projection(
            session_factory=self.session_factory,
        )

        self.assertEqual(projection.safe_entity_count, 1)
        self.assertEqual(len(projection.items), 1)
        item = projection.items[0]
        self.assertEqual(item.entity_id, self.entity.entity_id)
        self.assertEqual(item.entity_type, EntityType.ORGANIZATION)
        self.assertEqual(item.canonical_name, "united nations")
        self.assertEqual(
            item.canonical_entity_candidate_id,
            self.right.id,
        )
        self.assertEqual(
            tuple(
                candidate.entity_candidate_id
                for candidate in item.candidates
            ),
            (self.left.id, self.right.id),
        )
        self.assertEqual(
            item.candidates[0].assigned_by_alias_decision_id,
            self.approval.id,
        )
        link = item.active_resolutions[0]
        self.assertEqual(link.proposal_id, self.proposal.id)
        self.assertEqual(
            link.latest_alias_decision_id,
            self.approval.id,
        )
        self.assertEqual(link.latest_revision, 1)

    def test_new_review_immediately_removes_entity_from_projection(
            self,
    ) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.session.commit()

        projection = get_safe_entity_projection(
            session_factory=self.session_factory,
        )

        self.assertEqual(projection.safe_entity_count, 0)
        self.assertEqual(projection.items, ())

    def test_invalid_extension_blocks_the_complete_entity(self) -> None:
        third = self._candidate("un organisation")
        extension = self._proposal(self.left, third)
        self._decide(extension, AliasDecisionStatus.APPROVED)
        EntityResolutionService(
            self.session
        ).resolve_approved_alias(proposal_id=extension.id)
        self._decide(extension, AliasDecisionStatus.REJECTED)
        self.session.commit()

        projection = get_safe_entity_projection(
            session_factory=self.session_factory,
        )

        self.assertEqual(projection.safe_entity_count, 0)
        self.assertEqual(projection.items, ())

    def test_reapproval_requires_reapplication_before_projection(
            self,
    ) -> None:
        second = self._decide(
            self.proposal,
            AliasDecisionStatus.APPROVED,
        )
        self.session.commit()

        pending = get_safe_entity_projection(
            session_factory=self.session_factory,
        )
        self.assertEqual(pending.items, ())

        EntityResolutionService(
            self.session
        ).resolve_approved_alias(proposal_id=self.proposal.id)
        self.session.commit()
        active = get_safe_entity_projection(
            session_factory=self.session_factory,
        )

        self.assertEqual(active.safe_entity_count, 1)
        self.assertEqual(
            active.items[0]
            .active_resolutions[0]
            .latest_alias_decision_id,
            second.id,
        )

    def test_type_filter_and_limit_have_stable_semantics(self) -> None:
        second_left = self._candidate(
            "a. smith",
            entity_type=EntityType.PERSON,
        )
        second_right = self._candidate(
            "alice smith",
            entity_type=EntityType.PERSON,
        )
        second_proposal = self._proposal(
            second_left,
            second_right,
        )
        self._decide(
            second_proposal,
            AliasDecisionStatus.APPROVED,
        )
        second_entity = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=second_proposal.id,
            canonical_candidate_id=second_right.id,
        )
        self.session.commit()

        filtered = get_safe_entity_projection(
            limit=1,
            entity_type=EntityType.PERSON,
            session_factory=self.session_factory,
        )
        unfiltered = get_safe_entity_projection(
            limit=1,
            session_factory=self.session_factory,
        )

        self.assertEqual(filtered.safe_entity_count, 1)
        self.assertEqual(
            filtered.items[0].entity_id,
            second_entity.entity_id,
        )
        self.assertEqual(unfiltered.safe_entity_count, 2)
        self.assertEqual(
            unfiltered.items[0].entity_id,
            self.entity.entity_id,
        )

    def test_projection_is_read_only_and_validates_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit"):
            get_safe_entity_projection(
                limit=0,
                session_factory=self.session_factory,
            )

        before = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )
        projection = get_safe_entity_projection(
            session_factory=self.session_factory,
        )
        self.session.expire_all()
        after = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )

        self.assertEqual(after, before)
        self.session.close()
        self.assertEqual(
            projection.items[0].canonical_name,
            "united nations",
        )
        self.session = self.session_factory()

    def _candidate(
            self,
            text: str,
            *,
            entity_type: EntityType = EntityType.ORGANIZATION,
    ) -> EntityCandidate:
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
            entity_type=entity_type,
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
            entity_type=left.entity_type,
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
