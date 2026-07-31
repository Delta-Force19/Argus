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
    DerivedArtifact,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
    EntityResolutionEvidence,
)
from argus.documents import DerivedArtifactType
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageStatus,
    get_document_entity_coverage,
    get_document_entity_coverage_batch,
)
from argus.services.entity_resolution_service import (
    EntityResolutionService,
)


class DocumentEntityCoverageServiceTests(unittest.TestCase):
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
        self.unassigned = self._candidate(
            "new york",
            surface_text="New York",
            start_char=40,
            entity_type=EntityType.LOCATION,
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

    def test_counts_safe_and_unassigned_candidates(self) -> None:
        report = get_document_entity_coverage(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.document_version_id, 1)
        self.assertEqual(report.document_id, 1)
        self.assertEqual(report.version_number, 1)
        self.assertEqual(report.candidate_count, 3)
        self.assertEqual(
            self._counts(report),
            {
                DocumentEntityCoverageStatus.SAFE_RESOLVED: 2,
                DocumentEntityCoverageStatus.NOT_ENTITY: 0,
                DocumentEntityCoverageStatus.UNASSIGNED: 1,
                DocumentEntityCoverageStatus.BLOCKED: 0,
                DocumentEntityCoverageStatus.INVALID_PROVENANCE: 0,
            },
        )
        self.assertEqual(
            tuple(item.entity_candidate_id for item in report.items),
            (self.left.id, self.right.id, self.unassigned.id),
        )
        self.assertEqual(
            report.items[0].assigned_by_alias_decision_id,
            self.approval.id,
        )
        self.assertIsNone(report.items[2].entity_id)

    def test_latest_review_blocks_all_assigned_candidates(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.session.commit()

        report = get_document_entity_coverage(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(
            self._counts(report)[
                DocumentEntityCoverageStatus.BLOCKED
            ],
            2,
        )
        blocked = tuple(
            item
            for item in report.items
            if item.status is DocumentEntityCoverageStatus.BLOCKED
        )
        self.assertEqual(
            blocked[0].blocking_validities[0].value,
            "needs_review",
        )
        self.assertEqual(
            {item.entity_id for item in blocked},
            {self.entity.entity_id},
        )

    def test_invalid_mention_provenance_is_visible(self) -> None:
        other_version = self._document_version(
            document_id=2,
            raw_artifact_id=2,
        )
        mention = self.session.get(
            EntityMention,
            self.unassigned.entity_mention_id,
        )
        mention.document_version_id = other_version.id
        self.session.commit()

        report = get_document_entity_coverage(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )

        item = next(
            item
            for item in report.items
            if item.entity_candidate_id == self.unassigned.id
        )
        self.assertIs(
            item.status,
            DocumentEntityCoverageStatus.INVALID_PROVENANCE,
        )
        self.assertIn("different documents", item.provenance_issue)

    def test_filter_precedes_counts_and_limit_only_bounds_items(
            self,
    ) -> None:
        report = get_document_entity_coverage(
            document_version_id=self.document_version.id,
            limit=1,
            entity_type=EntityType.ORGANIZATION,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.candidate_count, 2)
        self.assertEqual(len(report.items), 1)
        self.assertEqual(
            self._counts(report)[
                DocumentEntityCoverageStatus.SAFE_RESOLVED
            ],
            2,
        )
        self.assertEqual(
            self._counts(report)[
                DocumentEntityCoverageStatus.UNASSIGNED
            ],
            0,
        )

    def test_missing_version_and_invalid_limits_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "document_version_id"):
            get_document_entity_coverage(
                document_version_id=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            get_document_entity_coverage(
                document_version_id=self.document_version.id,
                limit=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            get_document_entity_coverage(
                document_version_id=999,
                session_factory=self.session_factory,
            )

    def test_audit_is_read_only_and_detached(self) -> None:
        before = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )
        report = get_document_entity_coverage(
            document_version_id=self.document_version.id,
            session_factory=self.session_factory,
        )
        after = self.session.scalar(
            select(func.count()).select_from(
                EntityResolutionEvidence
            )
        )

        self.assertEqual(after, before)
        self.session.close()
        self.assertEqual(report.items[0].surface_text, "UN")
        self.session = self.session_factory()

    def test_batch_uses_one_snapshot_and_includes_empty_versions(
            self,
    ) -> None:
        empty = self._document_version(
            document_id=2,
            raw_artifact_id=2,
        )
        self.session.commit()

        reports = get_document_entity_coverage_batch(
            session_factory=self.session_factory,
        )

        self.assertEqual(
            tuple(item.document_version_id for item in reports),
            (self.document_version.id, empty.id),
        )
        self.assertEqual(reports[0].candidate_count, 3)
        self.assertEqual(reports[1].candidate_count, 0)
        self.assertEqual(reports[1].items, ())

    def test_batch_rejects_invalid_item_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "item_limit"):
            get_document_entity_coverage_batch(
                item_limit=0,
                session_factory=self.session_factory,
            )

    @staticmethod
    def _counts(report) -> dict[
        DocumentEntityCoverageStatus,
        int,
    ]:
        return {
            item.status: item.count
            for item in report.counts_by_status
        }

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
            entity_type: EntityType = EntityType.ORGANIZATION,
    ) -> EntityCandidate:
        end_char = start_char + len(surface_text)
        source_text = (
            (" " * start_char)
            + surface_text
            + " appeared in the source."
        )
        text_artifact = self._artifact(
            DerivedArtifactType.EXTRACTED_TEXT,
            payload={
                "text": source_text,
                "character_count": len(source_text),
            },
        )
        mention_artifact = self._artifact(
            DerivedArtifactType.ENTITY_MENTIONS,
            payload={
                "input_artifact_id": text_artifact.id,
                "input_content_hash": text_artifact.content_hash,
            },
        )
        mention = EntityMention(
            derived_artifact_id=mention_artifact.id,
            document_version_id=self.document_version.id,
            entity_type=entity_type,
            source_label=entity_type.value.upper(),
            surface_text=surface_text,
            normalized_text=surface_text.casefold(),
            start_char=start_char,
            end_char=end_char,
        )
        self.session.add(mention)
        self.session.flush()
        candidate_artifact = self._artifact(
            DerivedArtifactType.ENTITY_CANDIDATES,
            payload={
                "input_artifact_id": mention_artifact.id,
                "input_content_hash": mention_artifact.content_hash,
            },
        )
        row = EntityCandidate(
            derived_artifact_id=candidate_artifact.id,
            entity_mention_id=mention.id,
            document_version_id=self.document_version.id,
            entity_type=entity_type,
            canonical_text=text,
            context_text=f"{surface_text} appeared in the source.",
            context_start_char=start_char,
            context_end_char=end_char,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _artifact(
            self,
            artifact_type: DerivedArtifactType,
            *,
            payload: dict[str, object],
    ) -> DerivedArtifact:
        index = (
            self.session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            )
            or 0
        ) + 1
        row = DerivedArtifact(
            document_version_id=self.document_version.id,
            artifact_type=artifact_type,
            method=f"test-{artifact_type.value}",
            method_version="1",
            schema_version="1",
            content_hash=f"{index:064x}",
            payload=payload,
            quality_limitations=[],
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
