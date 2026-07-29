from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from argus.documents import (
    DocumentIdentityConflict,
    DocumentType,
    DocumentVersionConflict,
)
from argus.models import (
    Document,
    DocumentVersion,
    RawArtifact,
)
from argus.storage.base_repository import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=Document,
        )

    def get_by_identity(
            self,
            *,
            identifier_scheme: str,
            identifier_value: str,
    ) -> Document | None:
        statement = (
            select(Document)
            .where(
                Document.identifier_scheme
                == identifier_scheme,
                Document.identifier_value
                == identifier_value,
            )
        )

        return self.session.scalar(statement)

    def get_or_create(
            self,
            *,
            identifier_scheme: str,
            identifier_value: str,
            document_type: DocumentType,
            source_id: int | None = None,
            title: str | None = None,
            language: str | None = None,
    ) -> Document:
        self._validate_identity(
            identifier_scheme=identifier_scheme,
            identifier_value=identifier_value,
        )
        existing = self.get_by_identity(
            identifier_scheme=identifier_scheme,
            identifier_value=identifier_value,
        )

        if existing is not None:
            self._validate_existing(
                document=existing,
                document_type=document_type,
                source_id=source_id,
            )

            if existing.title is None and title is not None:
                existing.title = title

            if existing.language is None and language is not None:
                existing.language = language

            return existing

        document = Document(
            source_id=source_id,
            document_type=document_type,
            identifier_scheme=identifier_scheme,
            identifier_value=identifier_value,
            title=title,
            language=language,
        )
        self.add(document)
        self.flush()

        return document

    @staticmethod
    def _validate_identity(
            *,
            identifier_scheme: str,
            identifier_value: str,
    ) -> None:
        if not identifier_scheme.strip():
            raise ValueError(
                "identifier_scheme must not be blank."
            )

        if not identifier_value.strip():
            raise ValueError(
                "identifier_value must not be blank."
            )

    @staticmethod
    def _validate_existing(
            *,
            document: Document,
            document_type: DocumentType,
            source_id: int | None,
    ) -> None:
        conflicts: list[str] = []

        if document.document_type is not document_type:
            conflicts.append("document_type")

        if document.source_id != source_id:
            conflicts.append("source_id")

        if conflicts:
            raise DocumentIdentityConflict(
                "Document identity conflicts on: "
                f"{', '.join(conflicts)}."
            )


class DocumentVersionRepository(
    BaseRepository[DocumentVersion]
):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=DocumentVersion,
        )

    def get_by_artifact(
            self,
            *,
            document_id: int,
            raw_artifact_id: int,
    ) -> DocumentVersion | None:
        statement = (
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id
                == document_id,
                DocumentVersion.raw_artifact_id
                == raw_artifact_id,
            )
        )

        return self.session.scalar(statement)

    def get_versions(
            self,
            document_id: int,
    ) -> list[DocumentVersion]:
        statement = (
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id
                == document_id,
            )
            .order_by(DocumentVersion.version_number.asc())
        )

        return list(self.session.scalars(statement).all())

    def register(
            self,
            *,
            document: Document,
            raw_artifact: RawArtifact,
            media_type: str | None = None,
            published_at: datetime | None = None,
    ) -> DocumentVersion:
        if document.id is None:
            raise ValueError(
                "document must be persisted before versioning."
            )

        if raw_artifact.id is None:
            raise ValueError(
                "raw_artifact must be persisted before versioning."
            )

        existing = self.get_by_artifact(
            document_id=document.id,
            raw_artifact_id=raw_artifact.id,
        )

        if existing is not None:
            self._validate_existing_version(
                version=existing,
                media_type=media_type,
                published_at=published_at,
            )
            return existing

        latest_number = self.session.scalar(
            select(func.max(DocumentVersion.version_number))
            .where(
                DocumentVersion.document_id == document.id
            )
        )
        version = DocumentVersion(
            document_id=document.id,
            raw_artifact_id=raw_artifact.id,
            version_number=(latest_number or 0) + 1,
            media_type=media_type,
            published_at=published_at,
        )
        self.add(version)
        self.flush()

        return version

    @classmethod
    def _validate_existing_version(
            cls,
            *,
            version: DocumentVersion,
            media_type: str | None,
            published_at: datetime | None,
    ) -> None:
        conflicts: list[str] = []

        if version.media_type != media_type:
            conflicts.append("media_type")

        if cls._as_utc(version.published_at) != cls._as_utc(
            published_at
        ):
            conflicts.append("published_at")

        if conflicts:
            raise DocumentVersionConflict(
                "Document version metadata conflicts on: "
                f"{', '.join(conflicts)}."
            )

    @staticmethod
    def _as_utc(
            value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)
