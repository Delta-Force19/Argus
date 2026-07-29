import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import (
    CanonicalizedEntityCandidate,
    EntityType,
    RecognizedEntityMention,
)
from argus.models import EntityCandidate
from argus.services.entity_candidate_audit_service import (
    get_entity_candidate_audit,
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


class EntityCandidateAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as session:
            self._add_run(
                session,
                suffix="one",
                language="en-US",
                title="First\nstory",
                forms=(
                    (EntityType.ORGANIZATION, "UN", "un"),
                    (
                        EntityType.ORGANIZATION,
                        "United Nations",
                        "united nations",
                    ),
                    (
                        EntityType.PERSON,
                        "António Guterres",
                        "antónio guterres",
                    ),
                    (EntityType.PERSON, "Guterres", "guterres"),
                    (EntityType.GROUP, "Syrian", "syrian"),
                    (EntityType.GROUP, "Syrians", "syrians"),
                ),
            )
            self._add_run(
                session,
                suffix="two",
                language="ru",
                title="Second story",
                forms=(
                    (EntityType.ORGANIZATION, "UN", "un"),
                    (EntityType.PERSON, "Guterres", "guterres"),
                ),
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_report_summarizes_persisted_candidates(self) -> None:
        report = get_entity_candidate_audit(
            top=3,
            examples=3,
            pairs=5,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.candidate_count, 8)
        self.assertEqual(report.artifact_count, 2)
        self.assertEqual(report.document_version_count, 2)
        self.assertEqual(
            [(item.name, item.count) for item in report.counts_by_language],
            [("en", 6), ("ru", 2)],
        )
        self.assertEqual(
            [(item.name, item.count) for item in report.counts_by_type],
            [("group", 2), ("organization", 3), ("person", 3)],
        )
        self.assertEqual(
            {
                (
                    item.canonical_text,
                    item.candidate_count,
                    item.document_count,
                )
                for item in report.frequent_candidates[:2]
            },
            {("un", 2, 2), ("guterres", 2, 2)},
        )
        self.assertEqual(report.densest_runs[0].candidate_count, 6)
        self.assertEqual(
            {item.entity_type for item in report.examples},
            {
                EntityType.GROUP,
                EntityType.ORGANIZATION,
                EntityType.PERSON,
            },
        )

    def test_alias_signals_are_review_only_and_evidence_bearing(self) -> None:
        report = get_entity_candidate_audit(
            pairs=5,
            session_factory=self.session_factory,
        )

        signals = {
            (item.left_text, item.right_text): item
            for item in report.alias_signals
        }
        acronym = signals[("un", "united nations")]
        person = signals[("antónio guterres", "guterres")]
        inflection = signals[("syrian", "syrians")]

        self.assertEqual(acronym.reason, "acronym")
        self.assertEqual(acronym.shared_document_count, 1)
        self.assertEqual(person.reason, "person_short_name")
        self.assertEqual(person.shared_document_count, 1)
        self.assertEqual(inflection.reason, "inflectional_variant")
        self.assertIn("UN", acronym.left_context)
        self.assertIn("United Nations", acronym.right_context)

    def test_zero_candidate_artifact_is_counted_as_a_run(self) -> None:
        with self.session_factory() as session:
            self._add_run(
                session,
                suffix="empty",
                language="en",
                title="No candidates",
                forms=(),
            )
            session.commit()

        report = get_entity_candidate_audit(
            session_factory=self.session_factory,
        )

        self.assertEqual(report.candidate_count, 8)
        self.assertEqual(report.artifact_count, 3)
        self.assertEqual(report.document_version_count, 3)
        self.assertEqual(report.densest_runs[-1].candidate_count, 0)

    def test_report_does_not_modify_database(self) -> None:
        with self.session_factory() as session:
            before = session.scalar(
                select(func.count()).select_from(EntityCandidate)
            )

        get_entity_candidate_audit(
            session_factory=self.session_factory,
        )

        with self.session_factory() as session:
            after = session.scalar(
                select(func.count()).select_from(EntityCandidate)
            )
        self.assertEqual(after, before)

    def test_limits_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "top"):
            get_entity_candidate_audit(
                top=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "examples"):
            get_entity_candidate_audit(
                examples=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "pairs"):
            get_entity_candidate_audit(
                pairs=0,
                session_factory=self.session_factory,
            )

    def _add_run(
            self,
            session: Session,
            *,
            suffix: str,
            language: str,
            title: str,
            forms: tuple[tuple[EntityType, str, str], ...],
    ) -> None:
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
                storage_key=f"sha256/{suffix}/" + suffix * 32,
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
            payload={"text": text, "character_count": len(text)},
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
            method="deterministic-entity-candidate-canonicalizer",
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


if __name__ == "__main__":
    unittest.main()
