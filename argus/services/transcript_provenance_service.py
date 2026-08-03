from collections.abc import Mapping

from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact, RawArtifact, TranscriptAcquisition


def transcript_provenance_issue(
        session: Session,
        artifact: DerivedArtifact,
) -> str | None:
    """Return a fail-closed issue for an invalid transcript provenance chain."""

    if artifact.artifact_type is not DerivedArtifactType.TRANSCRIPT:
        return None
    source = artifact.payload.get("source")
    if not isinstance(source, Mapping):
        return "Transcript payload has no structured source provenance."
    acquisition_id = _identifier(source.get("transcript_acquisition_id"))
    raw_artifact_id = _identifier(source.get("raw_artifact_id"))
    if acquisition_id is None or raw_artifact_id is None:
        return "Transcript source provenance has invalid identifiers."
    acquisition = session.get(TranscriptAcquisition, acquisition_id)
    raw_artifact = session.get(RawArtifact, raw_artifact_id)
    if acquisition is None or raw_artifact is None:
        return "Transcript source provenance references missing records."
    if artifact.document_version_id != acquisition.document_version_id:
        return "Transcript acquisition belongs to another document version."
    if acquisition.raw_artifact_id != raw_artifact.id:
        return "Transcript acquisition references another raw artifact."
    if (
            source.get("hash_algorithm") != raw_artifact.hash_algorithm
            or source.get("content_hash") != raw_artifact.content_hash
    ):
        return "Transcript source digest conflicts with its raw artifact."
    if artifact.payload.get("language") != acquisition.language:
        return "Transcript language conflicts with acquisition provenance."
    if (
            artifact.payload.get("transcript_kind")
            != acquisition.transcript_kind.value
            or artifact.payload.get("transcript_format")
            != acquisition.transcript_format.value
    ):
        return "Transcript type metadata conflicts with acquisition provenance."
    return None


def _identifier(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value
