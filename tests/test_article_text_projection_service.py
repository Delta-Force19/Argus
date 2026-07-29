import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.documents import (
    ArticleTextProjectionConflict,
    DerivedArtifactType,
    DocumentType,
)
from argus.models import Article, DerivedArtifact
from argus.services.article_text_projection_service import (
    ArticleTextProjectionService,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from argus.storage.raw_artifact_repository import RawArtifactRepository


class ArticleTextProjectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.document = DocumentRepository(self.session).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/article",
            document_type=DocumentType.ARTICLE,
        )
        raw = RawArtifactRepository(self.session).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/aa/" + "a" * 62,
                hash_algorithm="sha256",
                content_hash="a" * 64,
                byte_size=128,
            )
        )
        self.version = DocumentVersionRepository(
            self.session
        ).register(
            document=self.document,
            raw_artifact=raw,
            media_type="text/html",
        )
        self.article = Article(
            document_id=self.document.id,
            url="https://example.com/article",
            title="Example article",
        )
        self.session.add(self.article)
        self.session.flush()
        self.artifact = self._register_artifact()
        self.service = ArticleTextProjectionService(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _register_artifact(
            self,
            *,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            schema_version="1",
            payload=None,
    ) -> DerivedArtifact:
        if payload is None:
            payload = {
                "text": "Normalized document text",
                "character_count": 24,
            }
        return DerivedArtifactRepository(self.session).register(
            document_version=self.version,
            artifact_type=artifact_type,
            method="stub-extractor",
            method_version="1.0",
            schema_version=schema_version,
            payload=payload,
        )

    def test_project_populates_article_with_explicit_provenance(self) -> None:
        projected = self.service.project(
            article=self.article,
            artifact=self.artifact,
        )

        self.assertIs(projected, self.article)
        self.assertEqual(
            projected.content,
            "Normalized document text",
        )
        self.assertEqual(
            projected.content_derived_artifact_id,
            self.artifact.id,
        )

    def test_project_is_idempotent_for_same_artifact(self) -> None:
        first = self.service.project(
            article=self.article,
            artifact=self.artifact,
        )
        second = self.service.project(
            article=self.article,
            artifact=self.artifact,
        )

        self.assertIs(first, second)
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(Article)
            ),
            1,
        )

    def test_project_does_not_commit(self) -> None:
        self.session.commit()
        self.service.project(
            article=self.article,
            artifact=self.artifact,
        )
        self.session.rollback()

        stored = self.session.get(Article, self.article.id)
        self.assertIsNone(stored.content)
        self.assertIsNone(stored.content_derived_artifact_id)

    def test_project_rejects_legacy_content_without_provenance(self) -> None:
        self.article.content = "Legacy parser text"
        self.session.flush()

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "without derived-artifact provenance",
        ):
            self.service.project(
                article=self.article,
                artifact=self.artifact,
            )

    def test_project_rejects_text_from_another_document(self) -> None:
        other_document = DocumentRepository(
            self.session
        ).get_or_create(
            identifier_scheme="uri",
            identifier_value="https://example.com/other",
            document_type=DocumentType.ARTICLE,
        )
        other_raw = RawArtifactRepository(
            self.session
        ).get_or_create(
            StoredArtifact(
                storage_backend="filesystem",
                storage_key="sha256/bb/" + "b" * 62,
                hash_algorithm="sha256",
                content_hash="b" * 64,
                byte_size=64,
            )
        )
        other_version = DocumentVersionRepository(
            self.session
        ).register(
            document=other_document,
            raw_artifact=other_raw,
        )
        other_artifact = DerivedArtifactRepository(
            self.session
        ).register(
            document_version=other_version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method="stub-extractor",
            method_version="1.0",
            schema_version="1",
            payload={"text": "Other", "character_count": 5},
        )

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "another document",
        ):
            self.service.project(
                article=self.article,
                artifact=other_artifact,
            )

    def test_project_rejects_non_text_artifact(self) -> None:
        artifact = self._register_artifact(
            artifact_type=DerivedArtifactType.NORMALIZED_METADATA,
        )

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "EXTRACTED_TEXT",
        ):
            self.service.project(
                article=self.article,
                artifact=artifact,
            )

    def test_project_rejects_unsupported_schema(self) -> None:
        artifact = self._register_artifact(schema_version="2")

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "schema version",
        ):
            self.service.project(
                article=self.article,
                artifact=artifact,
            )

    def test_project_rejects_inconsistent_character_count(self) -> None:
        artifact = self._register_artifact(
            payload={"text": "Example", "character_count": 100},
        )

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "character count",
        ):
            self.service.project(
                article=self.article,
                artifact=artifact,
            )

    def test_project_rejects_unlinked_article(self) -> None:
        article = Article(
            url="https://example.com/unlinked",
            title="Unlinked article",
        )
        self.session.add(article)
        self.session.flush()

        with self.assertRaisesRegex(
                ArticleTextProjectionConflict,
                "not linked",
        ):
            self.service.project(
                article=article,
                artifact=self.artifact,
            )


if __name__ == "__main__":
    unittest.main()
