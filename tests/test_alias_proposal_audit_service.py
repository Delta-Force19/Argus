import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

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
from argus.services.alias_proposal_audit_service import (
    get_alias_proposal_audit,
)
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
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


class AliasProposalAuditServiceTests(unittest.TestCase):
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
    )

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as session:
            candidate_artifact = self._add_candidate_run(
                session,
                suffix="one",
                language="en-US",
                title="First\nstory",
                forms=self.FORMS,
            )
            AliasProposalGenerationService(
                session,
                proposer=DeterministicEntityAliasProposer(),
            ).generate(candidate_artifact)
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_report_summarizes_proposals_and_evidence(self) -> None:
        report = get_alias_proposal_audit(
            top=5,
            examples=5,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.proposal_count, 3)
        self.assertEqual(report.artifact_count, 1)
        self.assertEqual(report.document_version_count, 1)
        self.assertEqual(
            [(item.name, item.count) for item in report.counts_by_signal],
            [
                ("acronym", 1),
                ("inflectional_variant", 1),
                ("person_short_name", 1),
            ],
        )
        self.assertEqual(
            [
                (item.name, item.count)
                for item in report.counts_by_confidence_band
            ],
            [("high", 1), ("medium", 1), ("low", 1)],
        )
        self.assertEqual(report.runs[0].proposal_count, 3)
        self.assertEqual(report.runs[0].language, "en")
        self.assertEqual(report.runs[0].title, "First\nstory")

        examples = {
            item.signal_type: item for item in report.examples
        }
        acronym = examples[AliasSignalType.ACRONYM]
        self.assertEqual(acronym.confidence_score, 0.80)
        self.assertEqual(acronym.confidence_band, "high")
        self.assertIn("UN", acronym.left_context)
        self.assertIn("United Nations", acronym.right_context)
        self.assertEqual(acronym.shared_document_count, 1)
        self.assertTrue(report.quality_limitations)

    def test_empty_proposal_artifact_is_counted_as_a_run(self) -> None:
        with self.session_factory() as session:
            candidate_artifact = self._add_candidate_run(
                session,
                suffix="empty",
                language="ru",
                title="No pairs",
                forms=(),
            )
            AliasProposalGenerationService(
                session,
                proposer=DeterministicEntityAliasProposer(),
            ).generate(candidate_artifact)
            session.commit()

        report = get_alias_proposal_audit(
            top=5,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.proposal_count, 3)
        self.assertEqual(report.artifact_count, 2)
        self.assertEqual(report.document_version_count, 2)
        self.assertEqual(report.runs[-1].proposal_count, 0)

    def test_report_does_not_modify_database(self) -> None:
        with self.session_factory() as session:
            before_proposals = session.scalar(
                select(func.count()).select_from(AliasProposal)
            )
            before_artifacts = session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            )

        get_alias_proposal_audit(
            session_factory=self.session_factory,
        )

        with self.session_factory() as session:
            after_proposals = session.scalar(
                select(func.count()).select_from(AliasProposal)
            )
            after_artifacts = session.scalar(
                select(func.count()).select_from(DerivedArtifact)
            )
        self.assertEqual(after_proposals, before_proposals)
        self.assertEqual(after_artifacts, before_artifacts)

    def test_limits_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "top"):
            get_alias_proposal_audit(
                top=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "examples"):
            get_alias_proposal_audit(
                examples=0,
                session_factory=self.session_factory,
            )

    def _add_candidate_run(
            self,
            session: Session,
            *,
            suffix: str,
            language: str,
            title: str,
            forms: tuple[tuple[EntityType, str, str], ...],
    ) -> DerivedArtifact:
        text = " | ".join(surface for _, surface, _ in forms) or "None"
        document = DocumentRepository(session).get_or_create(
            identifier_scheme="uri",
            identifier_value=f"https://example.com/{suffix}",
            document_type=DocumentType.ARTICLE,
            language=language,
            title=title,
        )
        raw = RawArtifactRepository(session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key=f"sha256/{suffix}/" + suffix[0] * 64,
                hash_algorithm="sha256",
                content_hash=suffix[0] * 64,
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
        mention_artifact = DerivedArtifactRepository(session).register(
            document_version=version,
            artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
            method="stub-ner",
            method_version="stub-en@1",
            schema_version="1",
            payload={
                "input_artifact_id": text_artifact.id,
                "input_content_hash": text_artifact.content_hash,
                "mentions": [],
            },
        )
        mentions = []
        cursor = 0
        for entity_type, surface, normalized in forms:
            start = text.index(surface, cursor)
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
        mention_rows = EntityMentionRepository(session).register(
            artifact=mention_artifact,
            mentions=tuple(mentions),
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
                for mention in mention_rows
            ),
        )
        return candidate_artifact


if __name__ == "__main__":
    unittest.main()
