from sqlalchemy.orm import Session

from argus.acquisition import RawArtifactStore
from argus.documents import DerivedArtifactType
from argus.extraction import TextExtractor
from argus.models import DerivedArtifact, DocumentVersion, RawArtifact
from argus.storage.derived_artifact_repository import DerivedArtifactRepository


class TextExtractionService:
    """Create a reproducible extracted-text artifact from a document version.

    The service never commits. The caller owns the surrounding transaction.
    """

    SCHEMA_VERSION = "1"

    def __init__(
            self,
            session: Session,
            *,
            artifact_store: RawArtifactStore,
            extractor: TextExtractor,
    ) -> None:
        self._session = session
        self._artifact_store = artifact_store
        self._extractor = extractor
        self._derived_repository = DerivedArtifactRepository(session)

    def extract(self, document_version: DocumentVersion) -> DerivedArtifact:
        raw_artifact = self._load_raw_artifact(document_version)
        if raw_artifact.storage_backend != self._artifact_store.storage_backend:
            raise ValueError(
                "Raw artifact storage backend does not match the configured store."
            )

        content = self._artifact_store.read(raw_artifact.storage_key)
        if len(content) != raw_artifact.byte_size:
            raise ValueError(
                "Stored artifact byte size does not match database metadata."
            )

        extracted = self._extractor.extract(
            content,
            media_type=document_version.media_type,
        )
        return self._derived_repository.register(
            document_version=document_version,
            artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
            method=self._extractor.method,
            method_version=self._extractor.method_version,
            schema_version=self.SCHEMA_VERSION,
            payload={
                "text": extracted.text,
                "character_count": len(extracted.text),
            },
            quality_limitations=extracted.quality_limitations,
        )

    def _load_raw_artifact(
            self,
            document_version: DocumentVersion,
    ) -> RawArtifact:
        if document_version.id is None:
            raise ValueError(
                "document_version must be persisted before text extraction."
            )
        raw_artifact = self._session.get(
            RawArtifact,
            document_version.raw_artifact_id,
        )
        if raw_artifact is None:
            raise ValueError("Document version raw artifact does not exist.")
        return raw_artifact
