import unittest

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
    ManualCandidateResolutionDecision,
)
from argus.models import (
    CandidateResolutionDecision,
    CandidateResolutionEvidence,
    CandidateResolutionExclusion,
    DerivedArtifact,
    Document,
    DocumentVersion,
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityMention,
    RawArtifact,
)
from argus.services.candidate_resolution_service import (
    CandidateResolutionService,
    resolve_candidate_identity,
)
from argus.services.document_entity_readiness_service import (
    get_document_entity_readiness,
)
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageStatus,
    get_document_entity_coverage,
)
from argus.services.document_analysis_input_service import (
    get_document_analysis_input,
)
from argus.services.analysis_run_service import (
    build_analysis_input_manifest,
)
from argus.services.candidate_not_entity_audit_service import (
    CandidateNotEntityValidity,
)
from argus.services.entity_registry_audit_service import (
    EntityResolutionValidity,
    get_entity_registry_audit,
)


class CandidateResolutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.version = self._document_version()
        self.text = "UN met UN officials."
        self.text_artifact = self._artifact(
            DerivedArtifactType.EXTRACTED_TEXT,
            payload={"text": self.text},
        )
        self.mention_artifact = self._artifact(
            DerivedArtifactType.ENTITY_MENTIONS,
            payload={
                "input_artifact_id": self.text_artifact.id,
                "input_content_hash": self.text_artifact.content_hash,
            },
        )
        self.candidate_artifact = self._artifact(
            DerivedArtifactType.ENTITY_CANDIDATES,
            payload={
                "input_artifact_id": self.mention_artifact.id,
                "input_content_hash": self.mention_artifact.content_hash,
            },
        )
        self.first = self._candidate("UN", 0, 2, "un")
        self.second = self._candidate("UN", 7, 9, "un")
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_creates_entity_from_one_candidate_and_makes_it_safe(self) -> None:
        result = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.commit()

        self.assertTrue(result.entity_created)
        self.assertEqual(result.matched_candidate_ids, (self.first.id,))
        entity = self.session.get(Entity, result.entity_id)
        self.assertIsNone(entity.created_from_alias_decision_id)
        self.assertEqual(
            entity.created_from_candidate_resolution_decision_id,
            result.decision_id,
        )
        assignment = self.session.scalar(
            select(EntityCandidateAssignment).where(
                EntityCandidateAssignment.entity_candidate_id
                == self.first.id
            )
        )
        self.assertIsNone(assignment.assigned_by_alias_decision_id)
        self.assertEqual(
            assignment.assigned_by_candidate_resolution_decision_id,
            result.decision_id,
        )
        audit = get_entity_registry_audit(
            session_factory=self.session_factory,
        )
        self.assertEqual(audit.safe_entity_count, 1)
        self.assertEqual(
            audit.candidate_items[0].validity,
            EntityResolutionValidity.ACTIVE,
        )

    def test_exact_canonical_scope_is_explicit_and_type_bounded(self) -> None:
        result = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.EXACT_CANONICAL,
        )

        self.assertEqual(
            result.matched_candidate_ids,
            (self.first.id, self.second.id),
        )
        self.assertEqual(
            result.newly_assigned_candidate_ids,
            (self.first.id, self.second.id),
        )
        assignments = tuple(
            self.session.scalars(
                select(EntityCandidateAssignment).order_by(
                    EntityCandidateAssignment.entity_candidate_id
                )
            )
        )
        self.assertEqual(len(assignments), 2)
        self.assertEqual(
            {item.entity_id for item in assignments},
            {result.entity_id},
        )

    def test_links_a_distinct_candidate_to_existing_entity(self) -> None:
        first = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        third = self._candidate("officials", 10, 19, "officials")

        linked = self._decide(
            third,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
            entity_id=first.entity_id,
        )

        self.assertFalse(linked.entity_created)
        self.assertEqual(linked.entity_id, first.entity_id)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Entity)
            ),
            1,
        )

    def test_revocation_blocks_entity_without_deleting_history(self) -> None:
        assigned = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        revoked = self._decide(
            self.first,
            status=CandidateResolutionStatus.REVOKED,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.commit()

        self.assertEqual(
            revoked.supersedes_decision_id,
            assigned.decision_id,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionDecision
                )
            ),
            2,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionEvidence
                )
            ),
            1,
        )
        audit = get_entity_registry_audit(
            session_factory=self.session_factory,
        )
        self.assertEqual(audit.safe_entity_count, 0)
        self.assertEqual(audit.blocked_entity_count, 1)
        self.assertEqual(
            audit.candidate_items[0].validity,
            EntityResolutionValidity.REVOKED,
        )

    def test_readiness_becomes_reachable_for_directly_resolved_document(
            self,
    ) -> None:
        self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.EXACT_CANONICAL,
        )
        self.session.commit()

        readiness = get_document_entity_readiness(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )

        self.assertTrue(readiness.ready_for_downstream_use)
        self.assertEqual(readiness.safe_resolved_count, 2)
        self.assertEqual(readiness.unassigned_count, 0)

    def test_not_entity_is_explicit_ready_outcome_without_entity(self) -> None:
        assigned = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        excluded = self._decide(
            self.second,
            status=CandidateResolutionStatus.NOT_ENTITY,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.commit()

        self.assertIsNone(excluded.entity_id)
        self.assertFalse(excluded.entity_created)
        self.assertEqual(excluded.matched_candidate_ids, (self.second.id,))
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Entity)
            ),
            1,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionExclusion
                )
            ),
            1,
        )

        coverage = get_document_entity_coverage(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        statuses = {
            item.entity_candidate_id: item.status
            for item in coverage.items
        }
        self.assertEqual(
            statuses,
            {
                self.first.id: DocumentEntityCoverageStatus.SAFE_RESOLVED,
                self.second.id: DocumentEntityCoverageStatus.NOT_ENTITY,
            },
        )
        readiness = get_document_entity_readiness(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        self.assertTrue(readiness.ready_for_downstream_use)
        self.assertEqual(readiness.safe_resolved_count, 1)
        self.assertEqual(readiness.not_entity_count, 1)
        self.assertEqual(readiness.unassigned_count, 0)

        audit = get_entity_registry_audit(
            session_factory=self.session_factory,
        )
        self.assertEqual(len(audit.not_entity_items), 1)
        self.assertEqual(
            audit.not_entity_items[0].validity,
            CandidateNotEntityValidity.ACTIVE,
        )
        self.assertEqual(audit.not_entity_items[0].reviewer, "Victor")
        self.assertEqual(assigned.entity_id, audit.candidate_items[0].entity_id)

    def test_exact_not_entity_scope_freezes_reviewed_candidates(self) -> None:
        result = self._decide(
            self.first,
            status=CandidateResolutionStatus.NOT_ENTITY,
            scope=CandidateResolutionScope.EXACT_CANONICAL,
        )
        later_artifact = self._artifact(
            DerivedArtifactType.ENTITY_CANDIDATES,
            payload={
                "input_artifact_id": self.mention_artifact.id,
                "input_content_hash": self.mention_artifact.content_hash,
            },
        )
        later = EntityCandidate(
            derived_artifact_id=later_artifact.id,
            entity_mention_id=self.first.entity_mention_id,
            document_version_id=self.version.id,
            entity_type=EntityType.ORGANIZATION,
            canonical_text="un",
            context_text=self.text,
            context_start_char=0,
            context_end_char=len(self.text),
        )
        self.session.add(later)
        self.session.commit()

        self.assertEqual(
            result.matched_candidate_ids,
            (self.first.id, self.second.id),
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionExclusion
                )
            ),
            2,
        )
        coverage = get_document_entity_coverage(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        by_id = {
            item.entity_candidate_id: item.status
            for item in coverage.items
        }
        self.assertEqual(
            by_id[later.id],
            DocumentEntityCoverageStatus.UNASSIGNED,
        )

    def test_not_entity_revocation_restores_unassigned_without_deletion(
            self,
    ) -> None:
        applied = self._decide(
            self.first,
            status=CandidateResolutionStatus.NOT_ENTITY,
            scope=CandidateResolutionScope.SINGLE,
        )
        revoked = self._decide(
            self.first,
            status=CandidateResolutionStatus.REVOKED,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.commit()

        self.assertEqual(revoked.supersedes_decision_id, applied.decision_id)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionExclusion
                )
            ),
            1,
        )
        coverage = get_document_entity_coverage(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        first = next(
            item for item in coverage.items
            if item.entity_candidate_id == self.first.id
        )
        self.assertEqual(first.status, DocumentEntityCoverageStatus.UNASSIGNED)
        audit = get_entity_registry_audit(
            session_factory=self.session_factory,
        )
        self.assertEqual(
            audit.not_entity_items[0].validity,
            CandidateNotEntityValidity.REVOKED,
        )

    def test_not_entity_is_in_atomic_analysis_input(self) -> None:
        self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        excluded = self._decide(
            self.second,
            status=CandidateResolutionStatus.NOT_ENTITY,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.commit()

        bundle = get_document_analysis_input(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        self.assertEqual(len(bundle.not_entity_resolutions), 1)
        item = bundle.not_entity_resolutions[0]
        self.assertEqual(item.entity_candidate_id, self.second.id)
        self.assertEqual(item.decision_id, excluded.decision_id)
        self.assertEqual(item.scope, "single")
        manifest = build_analysis_input_manifest(bundle)
        self.assertEqual(manifest["schema_version"], "document-analysis-input@2")
        self.assertEqual(
            manifest["not_entity_resolutions"][0]["decision_id"],
            excluded.decision_id,
        )

    def test_not_entity_rejects_assignment_conflicts(self) -> None:
        assigned = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        with self.assertRaisesRegex(ValueError, "must be revoked"):
            self._decide(
                self.first,
                status=CandidateResolutionStatus.NOT_ENTITY,
                scope=CandidateResolutionScope.SINGLE,
            )
        with self.assertRaisesRegex(ValueError, "entity_id"):
            self._decide(
                self.second,
                status=CandidateResolutionStatus.NOT_ENTITY,
                scope=CandidateResolutionScope.SINGLE,
                entity_id=assigned.entity_id,
            )

    def test_missing_seed_assignment_fails_closed_in_registry_audit(
            self,
    ) -> None:
        self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        self.session.execute(delete(EntityCandidateAssignment))
        self.session.commit()

        audit = get_entity_registry_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(audit.safe_entity_count, 0)
        self.assertEqual(audit.blocked_entity_count, 1)
        self.assertEqual(
            audit.candidate_items[0].validity,
            EntityResolutionValidity.PENDING_REAPPLICATION,
        )

    def test_scope_change_and_cross_entity_reassignment_are_rejected(
            self,
    ) -> None:
        first = self._decide(
            self.first,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        with self.assertRaisesRegex(ValueError, "scope cannot change"):
            self._decide(
                self.first,
                status=CandidateResolutionStatus.REVOKED,
                scope=CandidateResolutionScope.EXACT_CANONICAL,
            )

        second = self._decide(
            self.second,
            status=CandidateResolutionStatus.ASSIGNED,
            scope=CandidateResolutionScope.SINGLE,
        )
        with self.assertRaisesRegex(ValueError, "reassignment"):
            self._decide(
                self.first,
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.SINGLE,
                entity_id=second.entity_id,
            )
        self.assertNotEqual(first.entity_id, second.entity_id)

    def test_invalid_provenance_is_rejected_before_any_decision(self) -> None:
        self.candidate_artifact.payload = {
            **self.candidate_artifact.payload,
            "input_content_hash": "f" * 64,
        }

        with self.assertRaisesRegex(ValueError, "provenance is invalid"):
            self._decide(
                self.first,
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.SINGLE,
            )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(
                    CandidateResolutionDecision
                )
            ),
            0,
        )

    def test_application_boundary_commits_detached_result(self) -> None:
        candidate_id = self.first.id
        self.session.close()

        result = resolve_candidate_identity(
            candidate_id=candidate_id,
            decision=self._manual(
                CandidateResolutionStatus.ASSIGNED,
                CandidateResolutionScope.SINGLE,
            ),
            session_factory=self.session_factory,
        )

        self.assertTrue(result.entity_created)
        with self.session_factory() as session:
            self.assertIsNotNone(session.get(Entity, result.entity_id))
        self.session = self.session_factory()

    def _decide(
            self,
            candidate: EntityCandidate,
            *,
            status: CandidateResolutionStatus,
            scope: CandidateResolutionScope,
            entity_id: int | None = None,
    ):
        return CandidateResolutionService(self.session).decide(
            candidate_id=candidate.id,
            entity_id=entity_id,
            decision=self._manual(status, scope),
        )

    @staticmethod
    def _manual(
            status: CandidateResolutionStatus,
            scope: CandidateResolutionScope,
    ) -> ManualCandidateResolutionDecision:
        return ManualCandidateResolutionDecision(
            status=status,
            scope=scope,
            reason="Reviewed exact candidate provenance and context.",
            reviewer="Victor",
        )

    def _document_version(self) -> DocumentVersion:
        raw = RawArtifact(
            hash_algorithm="sha256",
            content_hash="a" * 64,
            byte_size=64,
            storage_backend="test",
            storage_key="candidate-resolution.html",
        )
        document = Document(
            document_type=DocumentType.ARTICLE,
            identifier_scheme="url",
            identifier_value="https://example.test/candidate-resolution",
            title="Candidate resolution",
            language="en",
        )
        self.session.add_all((raw, document))
        self.session.flush()
        version = DocumentVersion(
            document_id=document.id,
            raw_artifact_id=raw.id,
            version_number=1,
            media_type="text/html",
        )
        self.session.add(version)
        self.session.flush()
        return version

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
        artifact = DerivedArtifact(
            document_version_id=self.version.id,
            artifact_type=artifact_type,
            method=f"test-{artifact_type.value}",
            method_version="1",
            schema_version="1",
            content_hash=f"{index:064x}",
            payload=payload,
            quality_limitations=[],
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def _candidate(
            self,
            surface: str,
            start: int,
            end: int,
            canonical: str,
    ) -> EntityCandidate:
        mention = EntityMention(
            derived_artifact_id=self.mention_artifact.id,
            document_version_id=self.version.id,
            entity_type=EntityType.ORGANIZATION,
            source_label="ORG",
            surface_text=surface,
            normalized_text=surface.casefold(),
            start_char=start,
            end_char=end,
        )
        self.session.add(mention)
        self.session.flush()
        candidate = EntityCandidate(
            derived_artifact_id=self.candidate_artifact.id,
            entity_mention_id=mention.id,
            document_version_id=self.version.id,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=canonical,
            context_text=self.text,
            context_start_char=0,
            context_end_char=len(self.text),
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate
