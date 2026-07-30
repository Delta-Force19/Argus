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
    DocumentVersion,
    EntityCandidate,
    EntityMention,
    EntityResolutionEvidence,
)
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.document_entity_projection_service import (
    get_document_entity_projection,
)
from argus.services.entity_resolution_service import (
    EntityResolutionService,
)


class DocumentEntityProjectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.document_version = self._document_version()
        self.left = self._candidate(
            "un",
            surface_text="UN",
            start_char=0,
        )
        self.right = self._candidate(
            "united nations",
            surface_text="United Nations",
            start_char=20,
        )
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

    def test_projects_safe_entities_with_exact_document_occurrences(
            self,
    ) -> None:
        projection = get_document_entity_projection(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(
            projection.document_version_id,
            self.document_version.id,
        )
        self.assertEqual(projection.document_id, 1)
        self.assertEqual(projection.version_number, 1)
        self.assertEqual(projection.resolved_entity_count, 1)
        self.assertEqual(projection.resolved_occurrence_count, 2)
        self.assertEqual(len(projection.items), 1)

        item = projection.items[0]
        self.assertEqual(item.entity_id, self.entity.entity_id)
        self.assertEqual(item.entity_type, EntityType.ORGANIZATION)
        self.assertEqual(item.canonical_name, "united nations")
        self.assertEqual(
            tuple(
                occurrence.surface_text
                for occurrence in item.occurrences
            ),
            ("UN", "United Nations"),
        )
        self.assertEqual(
            tuple(
                occurrence.start_char
                for occurrence in item.occurrences
            ),
            (0, 20),
        )
        self.assertEqual(
            item.occurrences[0].assigned_by_alias_decision_id,
            self.approval.id,
        )
        self.assertEqual(
            item.active_resolutions[0].latest_alias_decision_id,
            self.approval.id,
        )

    def test_new_review_removes_all_document_occurrences(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.session.commit()

        projection = get_document_entity_projection(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(projection.resolved_entity_count, 0)
        self.assertEqual(projection.resolved_occurrence_count, 0)
        self.assertEqual(projection.items, ())

    def test_invalid_link_in_another_document_blocks_entity(self) -> None:
        other_version = self._document_version(
            document_id=2,
            raw_artifact_id=2,
        )
        third = self._candidate(
            "un organisation",
            surface_text="UN Organisation",
            start_char=0,
            document_version_id=other_version.id,
        )
        extension = self._proposal(self.left, third)
        self._decide(extension, AliasDecisionStatus.APPROVED)
        EntityResolutionService(
            self.session
        ).resolve_approved_alias(proposal_id=extension.id)
        self._decide(extension, AliasDecisionStatus.REJECTED)
        self.session.commit()

        projection = get_document_entity_projection(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(projection.items, ())

    def test_other_document_occurrences_are_not_leaked(self) -> None:
        other_version = self._document_version(
            document_id=2,
            raw_artifact_id=2,
        )
        third = self._candidate(
            "un organisation",
            surface_text="UN Organisation",
            start_char=0,
            document_version_id=other_version.id,
        )
        extension = self._proposal(self.left, third)
        self._decide(extension, AliasDecisionStatus.APPROVED)
        EntityResolutionService(
            self.session
        ).resolve_approved_alias(proposal_id=extension.id)
        self.session.commit()

        projection = get_document_entity_projection(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        item = projection.items[0]
        self.assertEqual(
            tuple(
                occurrence.entity_candidate_id
                for occurrence in item.occurrences
            ),
            (self.left.id, self.right.id),
        )
        self.assertEqual(len(item.active_resolutions), 2)

    def test_filter_and_limit_count_entities_before_bounding(self) -> None:
        person_left = self._candidate(
            "a. smith",
            surface_text="A. Smith",
            start_char=40,
            entity_type=EntityType.PERSON,
        )
        person_right = self._candidate(
            "alice smith",
            surface_text="Alice Smith",
            start_char=60,
            entity_type=EntityType.PERSON,
        )
        person_proposal = self._proposal(person_left, person_right)
        self._decide(
            person_proposal,
            AliasDecisionStatus.APPROVED,
        )
        person_entity = EntityResolutionService(
            self.session
        ).resolve_approved_alias(
            proposal_id=person_proposal.id,
            canonical_candidate_id=person_right.id,
        )
        self.session.commit()

        unfiltered = get_document_entity_projection(
            document_version_id=self.document_version.id,
            limit=1,
            session_factory=self.session_factory,
        )
        filtered = get_document_entity_projection(
            document_version_id=self.document_version.id,
            limit=1,
            entity_type=EntityType.PERSON,
            session_factory=self.session_factory,
        )

        self.assertEqual(unfiltered.resolved_entity_count, 2)
        self.assertEqual(
            unfiltered.items[0].entity_id,
            self.entity.entity_id,
        )
        self.assertEqual(filtered.resolved_entity_count, 1)
        self.assertEqual(
            filtered.items[0].entity_id,
            person_entity.entity_id,
        )

    def test_missing_version_and_invalid_limits_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "document_version_id"):
            get_document_entity_projection(
                document_version_id=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            get_document_entity_projection(
                document_version_id=self.document_version.id,
                limit=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            get_document_entity_projection(
                document_version_id=999,
                session_factory=self.session_factory,
            )

    def test_projection_is_read_only_and_detached(self) -> None:
        before = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )
        projection = get_document_entity_projection(
            document_version_id=self.document_version.id,
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
            projection.items[0].occurrences[0].surface_text,
            "UN",
        )
        self.session = self.session_factory()

    def test_inconsistent_mention_provenance_is_rejected(self) -> None:
        other_version = self._document_version(
            document_id=2,
            raw_artifact_id=2,
        )
        mention = self.session.get(
            EntityMention,
            self.left.entity_mention_id,
        )
        mention.document_version_id = other_version.id
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "another document"):
            get_document_entity_projection(
                document_version_id=self.document_version.id,
                session_factory=self.session_factory,
            )

    def _document_version(
            self,
            *,
            document_id: int = 1,
            raw_artifact_id: int = 1,
    ) -> DocumentVersion:
        row = DocumentVersion(
            document_id=document_id,
            raw_artifact_id=raw_artifact_id,
            version_number=1,
            media_type="text/html",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _candidate(
            self,
            text: str,
            *,
            surface_text: str,
            start_char: int,
            document_version_id: int | None = None,
            entity_type: EntityType = EntityType.ORGANIZATION,
    ) -> EntityCandidate:
        version_id = (
            document_version_id
            if document_version_id is not None
            else self.document_version.id
        )
        next_id = (
            self.session.scalar(
                select(func.count()).select_from(EntityMention)
            )
            or 0
        ) + 1
        end_char = start_char + len(surface_text)
        mention = EntityMention(
            derived_artifact_id=next_id,
            document_version_id=version_id,
            entity_type=entity_type,
            source_label=entity_type.value.upper(),
            surface_text=surface_text,
            normalized_text=surface_text.casefold(),
            start_char=start_char,
            end_char=end_char,
        )
        self.session.add(mention)
        self.session.flush()
        row = EntityCandidate(
            derived_artifact_id=mention.derived_artifact_id,
            entity_mention_id=mention.id,
            document_version_id=version_id,
            entity_type=entity_type,
            canonical_text=text,
            context_text=f"{surface_text} appeared in the source.",
            context_start_char=start_char,
            context_end_char=end_char,
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
            document_version_id=left.document_version_id,
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
