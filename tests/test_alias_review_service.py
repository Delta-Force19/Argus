import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from argus.database import Base
from argus.knowledge import (
    AliasDecisionStatus,
    AliasSignalType,
    EntityType,
)
from argus.models import AliasDecision, AliasProposal, EntityCandidate
from argus.services.alias_review_service import (
    get_alias_review_queue,
    record_alias_decision,
)


class AliasReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as session:
            self.proposal_ids = (
                self._add_proposal(session, "UN", "United Nations"),
                self._add_proposal(session, "US", "United States"),
                self._add_proposal(session, "ICC", "International Court"),
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_queue_lists_unreviewed_proposals_in_stable_order(self) -> None:
        report = get_alias_review_queue(
            limit=2,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.open_count, 3)
        self.assertEqual(
            [item.proposal_id for item in report.items],
            list(self.proposal_ids[:2]),
        )
        first = report.items[0]
        self.assertEqual(first.left_text, "UN")
        self.assertEqual(first.right_text, "United Nations")
        self.assertIn("UN", first.left_context)
        self.assertIsNone(first.latest_status)

    def test_queue_keeps_needs_review_and_hides_final_decisions(self) -> None:
        record_alias_decision(
            proposal_id=self.proposal_ids[0],
            status=AliasDecisionStatus.NEEDS_REVIEW,
            reason="Case-sensitive context needs checking.",
            reviewer="analyst-one",
            session_factory=self.session_factory,
        )
        record_alias_decision(
            proposal_id=self.proposal_ids[1],
            status=AliasDecisionStatus.REJECTED,
            reason="Ambiguous normalized form.",
            reviewer="analyst-two",
            session_factory=self.session_factory,
        )
        record_alias_decision(
            proposal_id=self.proposal_ids[2],
            status=AliasDecisionStatus.APPROVED,
            reason="Same organization in context.",
            reviewer="analyst-three",
            session_factory=self.session_factory,
        )

        report = get_alias_review_queue(
            session_factory=self.session_factory,
        )

        self.assertEqual(report.open_count, 1)
        self.assertEqual(report.items[0].proposal_id, self.proposal_ids[0])
        self.assertEqual(
            report.items[0].latest_status,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.assertEqual(report.items[0].latest_revision, 1)

    def test_record_commits_one_decision_and_appends_revisions(self) -> None:
        first = record_alias_decision(
            proposal_id=self.proposal_ids[0],
            status=AliasDecisionStatus.NEEDS_REVIEW,
            reason="  Need another source.  ",
            reviewer="  first-reviewer  ",
            session_factory=self.session_factory,
        )
        second = record_alias_decision(
            proposal_id=self.proposal_ids[0],
            status=AliasDecisionStatus.APPROVED,
            reason="Independent context confirms the expansion.",
            reviewer="second-reviewer",
            session_factory=self.session_factory,
        )

        self.assertEqual(first.revision, 1)
        self.assertEqual(first.reason, "Need another source.")
        self.assertEqual(first.reviewer, "first-reviewer")
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.supersedes_decision_id, first.decision_id)
        with self.session_factory() as session:
            decisions = list(
                session.scalars(
                    select(AliasDecision)
                    .where(
                        AliasDecision.alias_proposal_id
                        == self.proposal_ids[0]
                    )
                    .order_by(AliasDecision.revision.asc())
                )
            )
        self.assertEqual([row.id for row in decisions], [
            first.decision_id,
            second.decision_id,
        ])

    def test_failed_record_rolls_back(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            record_alias_decision(
                proposal_id=9999,
                status=AliasDecisionStatus.REJECTED,
                reason="Unknown proposal.",
                reviewer="reviewer",
                session_factory=self.session_factory,
            )

        with self.session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(AliasDecision)
            )
        self.assertEqual(count, 0)

    def test_queue_is_read_only_and_requires_positive_limit(self) -> None:
        with self.session_factory() as session:
            before = session.scalar(
                select(func.count()).select_from(AliasDecision)
            )

        get_alias_review_queue(session_factory=self.session_factory)

        with self.session_factory() as session:
            after = session.scalar(
                select(func.count()).select_from(AliasDecision)
            )
        self.assertEqual(after, before)
        with self.assertRaisesRegex(ValueError, "limit"):
            get_alias_review_queue(
                limit=0,
                session_factory=self.session_factory,
            )

    @staticmethod
    def _add_proposal(
            session: Session,
            left_text: str,
            right_text: str,
    ) -> int:
        sequence = (
            session.scalar(
                select(func.count()).select_from(AliasProposal)
            )
            or 0
        ) + 1
        left = EntityCandidate(
            derived_artifact_id=sequence,
            entity_mention_id=sequence * 2 - 1,
            document_version_id=sequence,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=left_text,
            context_text=f"{left_text} appeared in the source.",
            context_start_char=0,
            context_end_char=len(left_text),
        )
        right = EntityCandidate(
            derived_artifact_id=sequence,
            entity_mention_id=sequence * 2,
            document_version_id=sequence,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=right_text,
            context_text=f"{right_text} appeared in the source.",
            context_start_char=0,
            context_end_char=len(right_text),
        )
        session.add_all((left, right))
        session.flush()
        proposal = AliasProposal(
            derived_artifact_id=sequence,
            document_version_id=sequence,
            left_entity_candidate_id=left.id,
            right_entity_candidate_id=right.id,
            entity_type=EntityType.ORGANIZATION,
            left_canonical_text=left_text,
            right_canonical_text=right_text,
            signal_type=AliasSignalType.ACRONYM,
            confidence_score=0.80,
            confidence_basis="deterministic-heuristic-v1",
            rationale="Initialism in the same document.",
            left_occurrence_count=1,
            right_occurrence_count=1,
            shared_document_count=1,
        )
        session.add(proposal)
        session.flush()
        return proposal.id
