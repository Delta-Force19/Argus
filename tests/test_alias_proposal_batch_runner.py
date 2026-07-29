import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    CanonicalizedEntityCandidate,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import AliasProposal, DerivedArtifact
from argus.proposers import DeterministicEntityAliasProposer
from argus.services.alias_proposal_batch_runner import (
    AliasProposalBatchItemStatus,
    AliasProposalBatchRunner,
)
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


class SelectiveProposer(DeterministicEntityAliasProposer):
    def propose(self, candidates):
        if any(item.canonical_text == "broken" for item in candidates):
            raise RuntimeError("proposal generation failed")
        return super().propose(candidates)


def seed_candidate_artifact(
        session,
        *,
        number: int,
        forms: tuple[tuple[EntityType, str], ...],
) -> int:
    text = " | ".join(surface for _, surface in forms)
    document = DocumentRepository(session).get_or_create(
        identifier_scheme="uri",
        identifier_value=f"https://example.com/{number}",
        document_type=DocumentType.ARTICLE,
        language="en",
    )
    raw = RawArtifactRepository(session).get_or_create(
        StoredArtifact(
            storage_backend="filesystem",
            storage_key=f"sha256/{number}/" + str(number) * 64,
            hash_algorithm="sha256",
            content_hash=str(number) * 64,
            byte_size=len(text),
        )
    )
    version = DocumentVersionRepository(session).register(
        document=document,
        raw_artifact=raw,
    )
    text_artifact = DerivedArtifactRepository(session).register(
        document_version=version,
        artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
        method="stub-text",
        method_version="1",
        schema_version="1",
        payload={"text": text},
    )
    recognized = []
    cursor = 0
    for entity_type, surface in forms:
        start = text.index(surface, cursor)
        recognized.append(
            RecognizedEntityMention(
                entity_type=entity_type,
                source_label=entity_type.value.upper(),
                surface_text=surface,
                normalized_text=surface.casefold(),
                start_char=start,
                end_char=start + len(surface),
            )
        )
        cursor = start + len(surface)
    mention_artifact = DerivedArtifactRepository(session).register(
        document_version=version,
        artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
        method="stub-ner",
        method_version="1",
        schema_version="1",
        payload={
            "input_artifact_id": text_artifact.id,
            "input_content_hash": text_artifact.content_hash,
            "mentions": [],
        },
    )
    mentions = EntityMentionRepository(session).register(
        artifact=mention_artifact,
        mentions=tuple(recognized),
    )
    candidate_artifact = DerivedArtifactRepository(session).register(
        document_version=version,
        artifact_type=DerivedArtifactType.ENTITY_CANDIDATES,
        method="stub-canonicalizer",
        method_version="1",
        schema_version="1",
        payload={
            "input_artifact_id": mention_artifact.id,
            "input_content_hash": mention_artifact.content_hash,
            "decisions": [],
        },
    )
    EntityCandidateRepository(session).register(
        artifact=candidate_artifact,
        candidates=tuple(
            CanonicalizedEntityCandidate(
                entity_mention_id=mention.id,
                document_version_id=version.id,
                entity_type=mention.entity_type,
                canonical_text=mention.normalized_text,
                context_text=text,
                context_start_char=0,
                context_end_char=len(text),
            )
            for mention in mentions
        ),
    )
    return candidate_artifact.id


class AliasProposalBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.artifact_ids = self._seed_artifacts()
        self.runner = AliasProposalBatchRunner(
            self.session_factory,
            proposer=SelectiveProposer(),
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_artifacts(self) -> tuple[int, ...]:
        groups = (
            (
                (EntityType.ORGANIZATION, "UN"),
                (EntityType.ORGANIZATION, "United Nations"),
            ),
            ((EntityType.ORGANIZATION, "broken"),),
            ((EntityType.PERSON, "Athena"),),
        )
        ids = []
        with self.session_factory() as session:
            for number, forms in enumerate(groups):
                ids.append(
                    seed_candidate_artifact(
                        session,
                        number=number,
                        forms=forms,
                    )
                )
            session.commit()
        return tuple(ids)

    def test_batch_commits_successes_and_continues_after_failure(self) -> None:
        report = self.runner.run(self.artifact_ids)

        self.assertEqual(report.total_count, 3)
        self.assertEqual(report.processed_count, 2)
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.proposal_count, 1)
        self.assertEqual(
            [item.status for item in report.items],
            [
                AliasProposalBatchItemStatus.PROCESSED,
                AliasProposalBatchItemStatus.FAILED,
                AliasProposalBatchItemStatus.PROCESSED,
            ],
        )
        self.assertEqual(report.items[1].error_type, "RuntimeError")
        self.assertEqual(report.items[2].proposal_count, 0)

        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(AliasProposal)
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(DerivedArtifact)
                    .where(
                        DerivedArtifact.artifact_type
                        == DerivedArtifactType.ALIAS_PROPOSALS
                    )
                ),
                2,
            )

    def test_missing_artifact_is_reported_without_raising(self) -> None:
        report = self.runner.run((999,))

        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.items[0].error_type, "LookupError")
        self.assertIn("does not exist", report.items[0].error_message)


if __name__ == "__main__":
    unittest.main()
