import unittest

from sqlalchemy import create_engine, func, select
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
    DerivedArtifact,
    Document,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
    RawArtifact,
)
from argus.services.candidate_resolution_queue_service import (
    ExactCanonicalScopeState,
    get_candidate_resolution_queue,
)
from argus.services.candidate_resolution_service import (
    resolve_candidate_identity,
)


class CandidateResolutionQueueServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.first_version, first_artifacts = self._document(
            title="First document",
            identifier="https://example.test/first",
            text="UN met UN officials.",
        )
        self.first = self._candidate(
            first_artifacts,
            surface="UN",
            start=0,
            end=2,
            canonical="un",
        )
        self.second = self._candidate(
            first_artifacts,
            surface="UN",
            start=7,
            end=9,
            canonical="un",
        )
        self.second_version, second_artifacts = self._document(
            title="Second document",
            identifier="https://example.test/second",
            text="UN replied.",
        )
        self.third = self._candidate(
            second_artifacts,
            surface="UN",
            start=0,
            end=2,
            canonical="un",
        )
        self.session.commit()
        self.first_id = self.first.id
        self.third_id = self.third.id
        self.first_version_id = self.first_version.id

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_groups_one_document_with_corpus_scope_and_context(self) -> None:
        queue = get_candidate_resolution_queue(
            document_version_id=self.first_version.id,
            session_factory=self.session_factory,
        )

        self.assertEqual(queue.title, "First document")
        self.assertEqual(queue.language, "en")
        self.assertEqual(queue.readiness.unassigned_count, 2)
        self.assertEqual(queue.unresolved_group_count, 1)
        group = queue.groups[0]
        self.assertEqual(
            group.seed_entity_candidate_id,
            self.first.id,
        )
        self.assertEqual(group.document_candidate_count, 2)
        self.assertEqual(group.corpus_candidate_count, 3)
        self.assertEqual(group.corpus_unassigned_count, 3)
        self.assertEqual(group.corpus_invalid_provenance_count, 0)
        self.assertEqual(
            group.exact_scope_state,
            ExactCanonicalScopeState.NEW_ENTITY,
        )
        self.assertEqual(len(group.contexts), 2)
        self.assertEqual(group.contexts[0].context_text, "UN met UN officials.")

    def test_automatic_selection_prioritizes_fewest_unassigned(self) -> None:
        queue = get_candidate_resolution_queue(
            session_factory=self.session_factory,
        )

        self.assertEqual(
            queue.document_version_id,
            self.second_version.id,
        )
        self.assertEqual(queue.readiness.unassigned_count, 1)

    def test_reports_existing_entity_across_exact_scope(self) -> None:
        self.session.close()
        resolved = resolve_candidate_identity(
            candidate_id=self.third_id,
            decision=ManualCandidateResolutionDecision(
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.SINGLE,
                reason="Reviewed exact candidate provenance and context.",
                reviewer="Victor",
            ),
            session_factory=self.session_factory,
        )
        self.session = self.session_factory()

        queue = get_candidate_resolution_queue(
            document_version_id=self.first_version_id,
            contexts_per_group=1,
            session_factory=self.session_factory,
        )

        group = queue.groups[0]
        self.assertEqual(
            group.exact_scope_state,
            ExactCanonicalScopeState.EXTENDS_ENTITY,
        )
        self.assertEqual(group.assigned_entity_ids, (resolved.entity_id,))
        self.assertEqual(group.corpus_unassigned_count, 2)
        self.assertEqual(len(group.contexts), 1)

    def test_marks_exact_scope_with_invalid_corpus_provenance(self) -> None:
        artifact = self.session.get(
            DerivedArtifact,
            self.third.derived_artifact_id,
        )
        artifact.payload = {
            **artifact.payload,
            "input_content_hash": "f" * 64,
        }
        self.session.commit()

        queue = get_candidate_resolution_queue(
            document_version_id=self.first_version_id,
            session_factory=self.session_factory,
        )

        group = queue.groups[0]
        self.assertEqual(
            group.exact_scope_state,
            ExactCanonicalScopeState.INVALID_PROVENANCE,
        )
        self.assertEqual(group.corpus_invalid_provenance_count, 1)

    def test_returns_ready_document_without_unassigned_candidates(self) -> None:
        self.session.close()
        resolve_candidate_identity(
            candidate_id=self.first_id,
            decision=ManualCandidateResolutionDecision(
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.EXACT_CANONICAL,
                reason="Reviewed exact candidate provenance and context.",
                reviewer="Victor",
            ),
            session_factory=self.session_factory,
        )
        self.session = self.session_factory()

        queue = get_candidate_resolution_queue(
            document_version_id=self.first_version_id,
            session_factory=self.session_factory,
        )

        self.assertTrue(queue.readiness.ready_for_downstream_use)
        self.assertEqual(queue.readiness.unassigned_count, 0)
        self.assertEqual(queue.unresolved_group_count, 0)
        self.assertEqual(queue.groups, ())

    def test_exact_scope_reports_active_not_entity_conflict(self) -> None:
        self.session.close()
        resolve_candidate_identity(
            candidate_id=self.third_id,
            decision=ManualCandidateResolutionDecision(
                status=CandidateResolutionStatus.NOT_ENTITY,
                scope=CandidateResolutionScope.SINGLE,
                reason="Reviewed false-positive NER observation.",
                reviewer="Victor",
            ),
            session_factory=self.session_factory,
        )
        self.session = self.session_factory()

        queue = get_candidate_resolution_queue(
            document_version_id=self.first_version_id,
            session_factory=self.session_factory,
        )

        group = queue.groups[0]
        self.assertEqual(
            group.exact_scope_state,
            ExactCanonicalScopeState.CONFLICT,
        )
        self.assertEqual(group.corpus_not_entity_count, 1)
        self.assertEqual(group.corpus_unassigned_count, 2)

    def _document(
            self,
            *,
            title: str,
            identifier: str,
            text: str,
    ) -> tuple[
        DocumentVersion,
        tuple[DerivedArtifact, DerivedArtifact, str],
    ]:
        artifact_index = (
            self.session.scalar(
                select(func.count()).select_from(RawArtifact)
            )
            or 0
        ) + 1
        raw = RawArtifact(
            hash_algorithm="sha256",
            content_hash=f"{artifact_index:064x}",
            byte_size=len(text),
            storage_backend="test",
            storage_key=f"queue-{artifact_index}.html",
        )
        document = Document(
            document_type=DocumentType.ARTICLE,
            identifier_scheme="url",
            identifier_value=identifier,
            title=title,
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
        text_artifact = self._artifact(
            version,
            DerivedArtifactType.EXTRACTED_TEXT,
            payload={"text": text},
        )
        mention_artifact = self._artifact(
            version,
            DerivedArtifactType.ENTITY_MENTIONS,
            payload={
                "input_artifact_id": text_artifact.id,
                "input_content_hash": text_artifact.content_hash,
            },
        )
        candidate_artifact = self._artifact(
            version,
            DerivedArtifactType.ENTITY_CANDIDATES,
            payload={
                "input_artifact_id": mention_artifact.id,
                "input_content_hash": mention_artifact.content_hash,
            },
        )
        return version, (mention_artifact, candidate_artifact, text)

    def _artifact(
            self,
            version: DocumentVersion,
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
            document_version_id=version.id,
            artifact_type=artifact_type,
            method=f"test-{artifact_type.value}",
            method_version="1",
            schema_version="1",
            content_hash=f"{index + 100:064x}",
            payload=payload,
            quality_limitations=[],
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def _candidate(
            self,
            artifacts: tuple[DerivedArtifact, DerivedArtifact, str],
            *,
            surface: str,
            start: int,
            end: int,
            canonical: str,
    ) -> EntityCandidate:
        mention_artifact, candidate_artifact, text = artifacts
        mention = EntityMention(
            derived_artifact_id=mention_artifact.id,
            document_version_id=mention_artifact.document_version_id,
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
            derived_artifact_id=candidate_artifact.id,
            entity_mention_id=mention.id,
            document_version_id=candidate_artifact.document_version_id,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=canonical,
            context_text=text,
            context_start_char=0,
            context_end_char=len(text),
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate
