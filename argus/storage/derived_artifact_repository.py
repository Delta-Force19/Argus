import hashlib
import json
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.documents import (
    DerivedArtifactConflict,
    DerivedArtifactType,
)
from argus.models import DerivedArtifact, DocumentVersion
from argus.storage.base_repository import BaseRepository


class DerivedArtifactRepository(BaseRepository[DerivedArtifact]):
    """Persist immutable, reproducible outputs of document processing."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=DerivedArtifact)

    def register(
            self,
            *,
            document_version: DocumentVersion,
            artifact_type: DerivedArtifactType,
            method: str,
            method_version: str,
            schema_version: str,
            payload: Mapping[str, object],
            quality_limitations: Sequence[str] = (),
    ) -> DerivedArtifact:
        if document_version.id is None:
            raise ValueError(
                "document_version must be persisted before derivation."
            )

        normalized_method = self._required(method, "method")
        normalized_method_version = self._required(
            method_version, "method_version"
        )
        normalized_schema_version = self._required(
            schema_version, "schema_version"
        )
        normalized_payload = self._normalize_payload(payload)
        normalized_limitations = self._normalize_limitations(
            quality_limitations
        )
        content_hash = self._hash_payload(normalized_payload)

        existing = self._get_reproducible_output(
            document_version_id=document_version.id,
            artifact_type=artifact_type,
            method=normalized_method,
            method_version=normalized_method_version,
            schema_version=normalized_schema_version,
            content_hash=content_hash,
        )
        if existing is not None:
            if existing.payload != normalized_payload:
                raise DerivedArtifactConflict(
                    "Derived artifact hash collides with different payload."
                )
            if existing.quality_limitations != normalized_limitations:
                raise DerivedArtifactConflict(
                    "Derived artifact quality limitations conflict."
                )
            return existing

        artifact = DerivedArtifact(
            document_version_id=document_version.id,
            artifact_type=artifact_type,
            method=normalized_method,
            method_version=normalized_method_version,
            schema_version=normalized_schema_version,
            content_hash=content_hash,
            payload=normalized_payload,
            quality_limitations=normalized_limitations,
        )
        self.add(artifact)
        self.flush()
        return artifact

    def get_for_version(
            self,
            document_version_id: int,
            *,
            artifact_type: DerivedArtifactType | None = None,
    ) -> list[DerivedArtifact]:
        statement = select(DerivedArtifact).where(
            DerivedArtifact.document_version_id == document_version_id
        )
        if artifact_type is not None:
            statement = statement.where(
                DerivedArtifact.artifact_type == artifact_type
            )
        statement = statement.order_by(DerivedArtifact.id.asc())
        return list(self.session.scalars(statement).all())

    def _get_reproducible_output(
            self,
            *,
            document_version_id: int,
            artifact_type: DerivedArtifactType,
            method: str,
            method_version: str,
            schema_version: str,
            content_hash: str,
    ) -> DerivedArtifact | None:
        return self.session.scalar(
            select(DerivedArtifact).where(
                DerivedArtifact.document_version_id
                == document_version_id,
                DerivedArtifact.artifact_type == artifact_type,
                DerivedArtifact.method == method,
                DerivedArtifact.method_version == method_version,
                DerivedArtifact.schema_version == schema_version,
                DerivedArtifact.content_hash == content_hash,
            )
        )

    @staticmethod
    def _required(value: str, name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be blank.")
        return normalized

    @staticmethod
    def _normalize_payload(
            payload: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            serialized = json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("payload must be valid JSON data.") from error
        return json.loads(serialized)

    @staticmethod
    def _normalize_limitations(
            limitations: Sequence[str],
    ) -> list[str]:
        normalized = [item.strip() for item in limitations]
        if any(not item for item in normalized):
            raise ValueError(
                "quality_limitations must not contain blank values."
            )
        return normalized

    @staticmethod
    def _hash_payload(payload: Mapping[str, object]) -> str:
        canonical = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
