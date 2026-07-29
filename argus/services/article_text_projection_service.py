from sqlalchemy.orm import Session

from argus.documents import (
    ArticleTextProjectionConflict,
    DerivedArtifactType,
)
from argus.models import Article, DerivedArtifact, DocumentVersion


class ArticleTextProjectionService:
    """Expose derived text to the legacy article analysis pipeline.

    The projection is deliberately narrow and never commits. The caller owns
    the surrounding transaction.
    """

    SUPPORTED_SCHEMA_VERSION = "1"

    def __init__(self, session: Session) -> None:
        self._session = session

    def project(
            self,
            *,
            article: Article,
            artifact: DerivedArtifact,
    ) -> Article:
        self._require_persisted(article, artifact)
        self._validate_artifact_type(artifact)
        self._validate_document_identity(article, artifact)
        text = self._extract_text(artifact)

        if article.content_derived_artifact_id is not None:
            if (
                    article.content_derived_artifact_id == artifact.id
                    and article.content == text
            ):
                return article
            raise ArticleTextProjectionConflict(
                "Article content already references a different "
                "derived result."
            )

        if article.content is not None:
            raise ArticleTextProjectionConflict(
                "Article already contains text without derived-artifact "
                "provenance."
            )

        article.content = text
        article.content_derived_artifact_id = artifact.id
        self._session.flush()
        return article

    @staticmethod
    def _require_persisted(
            article: Article,
            artifact: DerivedArtifact,
    ) -> None:
        if article.id is None:
            raise ValueError("article must be persisted before projection.")
        if artifact.id is None:
            raise ValueError("artifact must be persisted before projection.")
        if article.document_id is None:
            raise ArticleTextProjectionConflict(
                "Article is not linked to a document."
            )

    @classmethod
    def _validate_artifact_type(
            cls,
            artifact: DerivedArtifact,
    ) -> None:
        if artifact.artifact_type != DerivedArtifactType.EXTRACTED_TEXT:
            raise ArticleTextProjectionConflict(
                "Only EXTRACTED_TEXT artifacts can populate article content."
            )
        if artifact.schema_version != cls.SUPPORTED_SCHEMA_VERSION:
            raise ArticleTextProjectionConflict(
                "Extracted-text schema version is not supported."
            )

    def _validate_document_identity(
            self,
            article: Article,
            artifact: DerivedArtifact,
    ) -> None:
        version = self._session.get(
            DocumentVersion,
            artifact.document_version_id,
        )
        if version is None:
            raise ArticleTextProjectionConflict(
                "Derived artifact document version does not exist."
            )
        if version.document_id != article.document_id:
            raise ArticleTextProjectionConflict(
                "Derived text belongs to another document."
            )

    @staticmethod
    def _extract_text(artifact: DerivedArtifact) -> str:
        text = artifact.payload.get("text")
        character_count = artifact.payload.get("character_count")
        if not isinstance(text, str) or not text.strip():
            raise ArticleTextProjectionConflict(
                "Extracted-text payload does not contain usable text."
            )
        if (
                not isinstance(character_count, int)
                or isinstance(character_count, bool)
                or character_count != len(text)
        ):
            raise ArticleTextProjectionConflict(
                "Extracted-text character count is inconsistent."
            )
        return text
