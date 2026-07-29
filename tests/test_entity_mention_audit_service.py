import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import EntityType, RecognizedEntityMention
from argus.models import DerivedArtifact, EntityMention
from argus.services.entity_mention_audit_service import (
    get_entity_mention_audit,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.entity_mention_repository import (
    EntityMentionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class EntityMentionAuditServiceTests(unittest.TestCase):
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
                mentions=(
                    self._mention(
                        EntityType.PERSON, "PERSON", "Alice", "alice", 0
                    ),
                    self._mention(
                        EntityType.ORGANIZATION, "ORG", "ACME", "acme", 10
                    ),
                    self._mention(
                        EntityType.PERSON, "PERSON", "Alice", "alice", 20
                    ),
                ),
            )
            self._add_run(
                session,
                suffix="two",
                language="ru",
                title="Вторая статья",
                mentions=(
                    self._mention(
                        EntityType.PERSON, "PER", "Alice", "alice", 0
                    ),
                ),
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_report_summarizes_persisted_mentions(self) -> None:
        report = get_entity_mention_audit(
            top=2,
            examples=2,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.mention_count, 4)
        self.assertEqual(report.artifact_count, 2)
        self.assertEqual(report.document_version_count, 2)
        self.assertEqual(
            [(item.name, item.count) for item in report.counts_by_language],
            [("en", 3), ("ru", 1)],
        )
        self.assertEqual(
            [(item.name, item.count) for item in report.counts_by_type],
            [("organization", 1), ("person", 3)],
        )
        self.assertEqual(
            (
                report.frequent_mentions[0].entity_type,
                report.frequent_mentions[0].normalized_text,
                report.frequent_mentions[0].mention_count,
                report.frequent_mentions[0].document_count,
            ),
            (EntityType.PERSON, "alice", 3, 2),
        )
        self.assertEqual(report.densest_runs[0].mention_count, 3)
        self.assertEqual(
            {item.entity_type for item in report.examples},
            {EntityType.ORGANIZATION, EntityType.PERSON},
        )

    def test_report_is_empty_when_no_mentions_exist(self) -> None:
        empty_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(empty_engine)
        factory = sessionmaker(bind=empty_engine)
        try:
            report = get_entity_mention_audit(
                session_factory=factory,
            )
            self.assertEqual(report.mention_count, 0)
            self.assertEqual(report.frequent_mentions, ())
            self.assertEqual(report.examples, ())
        finally:
            empty_engine.dispose()

    def test_zero_mention_artifact_is_counted_as_a_run(self) -> None:
        with self.session_factory() as session:
            self._add_run(
                session,
                suffix="empty",
                language="en",
                title="No entities",
                mentions=(),
            )
            session.commit()

        report = get_entity_mention_audit(
            top=10,
            session_factory=self.session_factory,
        )

        self.assertEqual(report.mention_count, 4)
        self.assertEqual(report.artifact_count, 3)
        self.assertEqual(report.document_version_count, 3)
        self.assertEqual(report.densest_runs[-1].mention_count, 0)

    def test_report_does_not_modify_database(self) -> None:
        with self.session_factory() as session:
            before = session.scalar(
                select(func.count()).select_from(EntityMention)
            )

        get_entity_mention_audit(
            session_factory=self.session_factory,
        )

        with self.session_factory() as session:
            after = session.scalar(
                select(func.count()).select_from(EntityMention)
            )
        self.assertEqual(after, before)

    def test_limits_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "top"):
            get_entity_mention_audit(
                top=0,
                session_factory=self.session_factory,
            )
        with self.assertRaisesRegex(ValueError, "examples"):
            get_entity_mention_audit(
                examples=0,
                session_factory=self.session_factory,
            )

    def _add_run(
            self,
            session: Session,
            *,
            suffix: str,
            language: str,
            title: str,
            mentions: tuple[RecognizedEntityMention, ...],
    ) -> None:
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
                content_hash=(suffix[0] * 64),
                byte_size=100,
            )
        )
        version = DocumentVersionRepository(session).register(
            document=document,
            raw_artifact=raw,
        )
        artifact = DerivedArtifactRepository(session).register(
            document_version=version,
            artifact_type=DerivedArtifactType.ENTITY_MENTIONS,
            method="stub-ner",
            method_version=f"stub-{language.split('-', 1)[0]}@1",
            schema_version="1",
            payload={
                "language": language.split("-", 1)[0],
                "mentions": [],
            },
        )
        EntityMentionRepository(session).register(
            artifact=artifact,
            mentions=mentions,
        )

    @staticmethod
    def _mention(
            entity_type: EntityType,
            label: str,
            surface: str,
            normalized: str,
            start: int,
    ) -> RecognizedEntityMention:
        return RecognizedEntityMention(
            entity_type=entity_type,
            source_label=label,
            surface_text=surface,
            normalized_text=normalized,
            start_char=start,
            end_char=start + len(surface),
        )


if __name__ == "__main__":
    unittest.main()
