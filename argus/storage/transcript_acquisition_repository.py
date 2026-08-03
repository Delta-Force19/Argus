from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import DocumentVersion, RawArtifact, TranscriptAcquisition
from argus.storage.base_repository import BaseRepository
from argus.transcripts import TranscriptFormat, TranscriptKind


class TranscriptAcquisitionRepository(BaseRepository[TranscriptAcquisition]):
    """Persist exact transcript acquisition provenance without committing."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=TranscriptAcquisition)

    def register(
            self,
            *,
            document_version: DocumentVersion,
            raw_artifact: RawArtifact,
            provider: str,
            provider_version: str,
            requested_location: str,
            retrieved_at: datetime,
            language: str,
            transcript_kind: TranscriptKind,
            transcript_format: TranscriptFormat,
            media_type: str,
            resolved_location: str | None = None,
            external_identifier: str | None = None,
    ) -> TranscriptAcquisition:
        if document_version.id is None or raw_artifact.id is None:
            raise ValueError(
                "document_version and raw_artifact must be persisted."
            )
        values = {
            "provider": provider,
            "provider_version": provider_version,
            "requested_location": requested_location,
            "language": language,
            "media_type": media_type,
        }
        normalized = {
            name: self._required(value, name)
            for name, value in values.items()
        }
        normalized_resolved = self._optional(
            resolved_location, "resolved_location"
        )
        normalized_external = self._optional(
            external_identifier, "external_identifier"
        )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware.")

        existing = self.session.scalar(
            select(TranscriptAcquisition).where(
                TranscriptAcquisition.document_version_id
                == document_version.id,
                TranscriptAcquisition.raw_artifact_id == raw_artifact.id,
                TranscriptAcquisition.provider == normalized["provider"],
                TranscriptAcquisition.provider_version
                == normalized["provider_version"],
                TranscriptAcquisition.requested_location
                == normalized["requested_location"],
                TranscriptAcquisition.retrieved_at == retrieved_at,
                TranscriptAcquisition.language == normalized["language"],
                TranscriptAcquisition.transcript_kind == transcript_kind,
                TranscriptAcquisition.transcript_format == transcript_format,
            )
        )
        if existing is not None:
            conflicts = []
            if existing.resolved_location != normalized_resolved:
                conflicts.append("resolved_location")
            if existing.external_identifier != normalized_external:
                conflicts.append("external_identifier")
            if existing.media_type != normalized["media_type"]:
                conflicts.append("media_type")
            if conflicts:
                raise ValueError(
                    "Transcript acquisition provenance conflicts on: "
                    + ", ".join(conflicts)
                    + "."
                )
            return existing

        acquisition = TranscriptAcquisition(
            document_version_id=document_version.id,
            raw_artifact_id=raw_artifact.id,
            provider=normalized["provider"],
            provider_version=normalized["provider_version"],
            requested_location=normalized["requested_location"],
            resolved_location=normalized_resolved,
            external_identifier=normalized_external,
            retrieved_at=retrieved_at,
            language=normalized["language"],
            transcript_kind=transcript_kind,
            transcript_format=transcript_format,
            media_type=normalized["media_type"],
        )
        self.add(acquisition)
        self.flush()
        return acquisition

    @staticmethod
    def _required(value: str, name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be blank.")
        return normalized

    @classmethod
    def _optional(cls, value: str | None, name: str) -> str | None:
        if value is None:
            return None
        return cls._required(value, name)
