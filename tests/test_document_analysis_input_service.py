import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
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
    Document,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
    EntityResolutionEvidence,
    RawArtifact,
)
from argus.services.alias_decision_service import AliasDecisionService
from argus.services.document_analysis_input_service import (
    get_document_analysis_input,
)
from argus.services.entity_resolution_service import (
    EntityResolutionService,
)


class DocumentAnalysisInputServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.session = self.session_factory()
        self.document, self.version = self._document_version()
        self.text = "UN works with the United Nations."
        self.text_artifact = self._artifact(
            DerivedArtifactType.EXTRACTED_TEXT,
            payload={
                "text": self.text,
                "character_count": len(self.text),
            },
            quality_limitations=["Boilerplate may remain."],
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
        self.left = self._candidate("UN", "un")
        self.right = self._candidate(
            "United Nations",
            "united nations",
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

    def test_builds_complete_atomic_detached_input(self) -> None:
        factory_calls = 0

        def counting_factory():
            nonlocal factory_calls
            factory_calls += 1
            return self.session_factory()

        bundle = get_document_analysis_input(
            document_version_id=self.version.id,
            session_factory=counting_factory,
        )

        self.assertEqual(factory_calls, 1)
        self.assertEqual(bundle.document.document_id, self.document.id)
        self.assertEqual(bundle.document.document_type, DocumentType.ARTICLE)
        self.assertEqual(bundle.document.raw_content_hash, "a" * 64)
        self.assertEqual(bundle.text.derived_artifact_id, self.text_artifact.id)
        self.assertEqual(bundle.text.text, self.text)
        self.assertEqual(bundle.text.character_count, len(self.text))
        self.assertEqual(
            bundle.text.quality_limitations,
            ("Boilerplate may remain.",),
        )
        self.assertTrue(bundle.readiness.ready_for_downstream_use)
        self.assertEqual(bundle.readiness.candidate_count, 2)
        self.assertEqual(bundle.entities.resolved_entity_count, 1)
        self.assertEqual(bundle.entities.resolved_occurrence_count, 2)
        self.assertEqual(
            bundle.entities.items[0].entity_id,
            self.entity.entity_id,
        )

        self.session.close()
        self.assertEqual(bundle.entities.items[0].canonical_name, "united nations")
        self.session = self.session_factory()

    def test_rejects_document_that_is_no_longer_ready(self) -> None:
        self._decide(
            self.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "status=blocked"):
            get_document_analysis_input(
                document_version_id=self.version.id,
                session_factory=self.session_factory,
            )

    def test_rejects_broken_artifact_hash_chain(self) -> None:
        self.candidate_artifact.payload = {
            **self.candidate_artifact.payload,
            "input_content_hash": "f" * 64,
        }
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "status=invalid"):
            get_document_analysis_input(
                document_version_id=self.version.id,
                session_factory=self.session_factory,
            )

    def test_rejects_multiple_text_inputs(self) -> None:
        other_text = self._artifact(
            DerivedArtifactType.EXTRACTED_TEXT,
            payload={
                "text": self.text,
                "character_count": len(self.text),
            },
        )
        other_mentions = self._artifact(
            DerivedArtifactType.ENTITY_MENTIONS,
            payload={
                "input_artifact_id": other_text.id,
                "input_content_hash": other_text.content_hash,
            },
        )
        other_candidates = self._artifact(
            DerivedArtifactType.ENTITY_CANDIDATES,
            payload={
                "input_artifact_id": other_mentions.id,
                "input_content_hash": other_mentions.content_hash,
            },
        )
        mention = self.session.get(EntityMention, self.right.entity_mention_id)
        mention.derived_artifact_id = other_mentions.id
        self.right.derived_artifact_id = other_candidates.id
        self.session.commit()

        with self.assertRaisesRegex(ValueError, "one text artifact"):
            get_document_analysis_input(
                document_version_id=self.version.id,
                session_factory=self.session_factory,
            )

    def test_invalid_version_id_and_missing_version_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            get_document_analysis_input(
                document_version_id=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            get_document_analysis_input(
                document_version_id=999,
                session_factory=self.session_factory,
            )

    def test_bundle_is_read_only(self) -> None:
        before = self.session.scalar(
            select(func.count()).select_from(EntityResolutionEvidence)
        )
        get_document_analysis_input(
            document_version_id=self.version.id,
            session_factory=self.session_factory,
        )
        after = self.session.scalar(
            select(func.count()).select_from(EntityResolutionEvidence)
        )
        self.assertEqual(after, before)

    def _document_version(self) -> tuple[Document, DocumentVersion]:
        raw = RawArtifact(
            hash_algorithm="sha256",
            content_hash="a" * 64,
            byte_size=128,
            storage_backend="test",
            storage_key="raw/article.html",
        )
        document = Document(
            document_type=DocumentType.ARTICLE,
            identifier_scheme="url",
            identifier_value="https://example.test/article",
            title="UN article",
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
        return document, version

    def _artifact(
            self,
            artifact_type: DerivedArtifactType,
            *,
            payload: dict[str, object],
            quality_limitations: list[str] | None = None,
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
            quality_limitations=quality_limitations or [],
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def _candidate(
            self,
            surface_text: str,
            canonical_text: str,
    ) -> EntityCandidate:
        start_char = self.text.index(surface_text)
        end_char = start_char + len(surface_text)
        mention = EntityMention(
            derived_artifact_id=self.mention_artifact.id,
            document_version_id=self.version.id,
            entity_type=EntityType.ORGANIZATION,
            source_label="ORG",
            surface_text=surface_text,
            normalized_text=surface_text.casefold(),
            start_char=start_char,
            end_char=end_char,
        )
        self.session.add(mention)
        self.session.flush()
        candidate = EntityCandidate(
            derived_artifact_id=self.candidate_artifact.id,
            entity_mention_id=mention.id,
            document_version_id=self.version.id,
            entity_type=EntityType.ORGANIZATION,
            canonical_text=canonical_text,
            context_text=self.text,
            context_start_char=0,
            context_end_char=len(self.text),
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def _proposal(
            self,
            first: EntityCandidate,
            second: EntityCandidate,
    ) -> AliasProposal:
        left, right = sorted((first, second), key=lambda item: item.id)
        proposal = AliasProposal(
            derived_artifact_id=900,
            document_version_id=self.version.id,
            left_entity_candidate_id=left.id,
            right_entity_candidate_id=right.id,
            entity_type=EntityType.ORGANIZATION,
            left_canonical_text=left.canonical_text,
            right_canonical_text=right.canonical_text,
            signal_type=AliasSignalType.ACRONYM,
            confidence_score=0.90,
            confidence_basis="test",
            rationale="Reviewed alias signal.",
            left_occurrence_count=1,
            right_occurrence_count=1,
            shared_document_count=1,
        )
        self.session.add(proposal)
        self.session.flush()
        return proposal

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
