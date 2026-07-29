import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    AliasSignalType,
    CanonicalizedEntityCandidate,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import AliasProposal, DerivedArtifact
from argus.proposers import DeterministicEntityAliasProposer
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
)
from argus.storage.alias_proposal_repository import AliasProposalRepository
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


class VersionedProposer(DeterministicEntityAliasProposer):
    def __init__(self, version: str) -> None:
        self.version = version

    @property
    def method_version(self) -> str:
        return self.version


class AliasProposalGenerationServiceTests(unittest.TestCase):
    FORMS = (
        (EntityType.ORGANIZATION, "UN", "un"),
        (
            EntityType.ORGANIZATION,
            "United Nations",
            "united nations",
        ),
        (EntityType.PERSON, "António Guterres", "antónio guterres"),
        (EntityType.PERSON, "Guterres", "guterres"),
        (EntityType.GROUP, "Syrian", "syrian"),
        (EntityType.GROUP, "Syrians", "syrians"),
        (EntityType.ORGANIZATION, "UN News", "un news"),
    )
    TEXT = " | ".join(surface for _, surface, _ in FORMS)

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

        document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/alias-proposals",
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
        self.version = DocumentVersionRepository(
            self.session
        ).register(document=document, raw_artifact=raw)
        self.text_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method="stub-text",
            method_version="1",
            schema_version="1",
            payload={"text": self.TEXT},
        )
        self.mention_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
            method="stub-ner",
            method_version="stub-en@1",
            schema_version="1",
            payload={
                "input_artifact_id": self.text_artifact.id,
                "input_content_hash": self.text_artifact.content_hash,
                "mentions": [],
            },
        )
        mentions = EntityMentionRepository(self.session).register(
            artifact=self.mention_artifact,
            mentions=self._mentions(),
        )
        self.candidate_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=self.version,
            artifact_type=DerivedArtifactType.ENTITY_CANDIDATES,
            method="stub-canonicalizer",
            method_version="1",
            schema_version="1",
            payload={
                "input_artifact_id": self.mention_artifact.id,
                "input_content_hash": self.mention_artifact.content_hash,
                "decisions": [],
            },
        )
        EntityCandidateRepository(self.session).register(
            artifact=self.candidate_artifact,
            candidates=tuple(
                CanonicalizedEntityCandidate(
                    entity_mention_id=mention.id,
                    document_version_id=self.version.id,
                    entity_type=mention.entity_type,
                    canonical_text=mention.normalized_text,
                    context_text=self.TEXT,
                    context_start_char=0,
                    context_end_char=len(self.TEXT),
                )
                for mention in mentions
            ),
        )
        self.session.commit()
        self.service = AliasProposalGenerationService(
            self.session,
            proposer=DeterministicEntityAliasProposer(),
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_generates_versioned_artifact_and_queryable_proposals(
            self,
    ) -> None:
        generation = self.service.generate(self.candidate_artifact)

        self.assertEqual(
            generation.artifact.artifact_type,
            DerivedArtifactType.ALIAS_PROPOSALS,
        )
        self.assertEqual(
            generation.artifact.payload["input_artifact_id"],
            self.candidate_artifact.id,
        )
        self.assertEqual(len(generation.proposals), 3)
        self.assertEqual(
            {item.signal_type for item in generation.proposals},
            {
                AliasSignalType.ACRONYM,
                AliasSignalType.PERSON_SHORT_NAME,
                AliasSignalType.INFLECTIONAL_VARIANT,
            },
        )

    def test_payload_preserves_evidence_and_limitations(self) -> None:
        generation = self.service.generate(self.candidate_artifact)

        proposal = generation.artifact.payload["proposals"][0]
        self.assertIn("left_context", proposal["evidence"])
        self.assertIn("right_context", proposal["evidence"])
        self.assertEqual(proposal["evidence"]["shared_document_count"], 1)
        self.assertTrue(any(
            "not calibrated probabilities" in limitation
            for limitation in generation.artifact.quality_limitations
        ))
        self.assertNotIn("entity_id", proposal)

    def test_generation_is_idempotent(self) -> None:
        first = self.service.generate(self.candidate_artifact)
        second = self.service.generate(self.candidate_artifact)

        self.assertEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(
            [item.id for item in first.proposals],
            [item.id for item in second.proposals],
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(AliasProposal)
            ),
            3,
        )

    def test_new_proposer_version_preserves_previous_result(self) -> None:
        first = self.service.generate(self.candidate_artifact)
        second = AliasProposalGenerationService(
            self.session,
            proposer=VersionedProposer("2"),
        ).generate(self.candidate_artifact)

        self.assertNotEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(AliasProposal)
            ),
            6,
        )

    def test_generation_does_not_commit(self) -> None:
        self.service.generate(self.candidate_artifact)
        self.session.rollback()

        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(AliasProposal)
            ),
            0,
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count())
                .select_from(DerivedArtifact)
                .where(
                    DerivedArtifact.artifact_type
                    == DerivedArtifactType.ALIAS_PROPOSALS
                )
            ),
            0,
        )

    def test_repository_queries_proposals_by_artifact(self) -> None:
        generation = self.service.generate(self.candidate_artifact)

        proposals = AliasProposalRepository(
            self.session
        ).get_for_artifact(generation.artifact.id)

        self.assertEqual(
            [item.id for item in proposals],
            [item.id for item in generation.proposals],
        )

    def test_rejects_non_candidate_artifact(self) -> None:
        with self.assertRaisesRegex(ValueError, "entity candidates"):
            self.service.generate(self.text_artifact)

    def _mentions(self) -> tuple[RecognizedEntityMention, ...]:
        cursor = 0
        mentions = []
        for entity_type, surface, normalized in self.FORMS:
            start = self.TEXT.index(surface, cursor)
            mentions.append(
                RecognizedEntityMention(
                    entity_type=entity_type,
                    source_label=entity_type.value.upper(),
                    surface_text=surface,
                    normalized_text=normalized,
                    start_char=start,
                    end_char=start + len(surface),
                )
            )
            cursor = start + len(surface)
        return tuple(mentions)


if __name__ == "__main__":
    unittest.main()
