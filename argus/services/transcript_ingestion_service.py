from dataclasses import dataclass
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import re

from sqlalchemy.orm import Session

from argus.acquisition import RawArtifactStore
from argus.config import RAW_ARTIFACT_DIRECTORY
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact, DocumentVersion, TranscriptAcquisition
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.storage.raw_artifact_repository import RawArtifactRepository
from argus.storage.transcript_acquisition_repository import (
    TranscriptAcquisitionRepository,
)
from argus.transcripts import TranscriptFormat, TranscriptKind


_TIMING_LINE = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}\s+-->\s+"
    r"(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}(?:\s+.*)?$"
)
_CUE_TAG = re.compile(r"<[^>]+>")
_BCP47 = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


@dataclass(frozen=True, slots=True)
class TranscriptIngestionResult:
    document_version_id: int
    transcript_acquisition_id: int
    raw_artifact_id: int
    transcript_artifact_id: int
    raw_content_hash: str
    text_content_hash: str
    character_count: int
    language: str
    transcript_kind: TranscriptKind
    transcript_format: TranscriptFormat
    quality_limitations: tuple[str, ...]


class TranscriptIngestionService:
    """Anchor transcript bytes and register their normalized text output.

    The service writes content-addressed bytes but never commits the database.
    The caller owns transaction boundaries.
    """

    METHOD = "deterministic-transcript-normalization"
    METHOD_VERSION = "1"
    SCHEMA_VERSION = "1"

    def __init__(
            self,
            session: Session,
            *,
            artifact_store: RawArtifactStore,
    ) -> None:
        self._session = session
        self._artifact_store = artifact_store
        self._raw_repository = RawArtifactRepository(session)
        self._acquisition_repository = TranscriptAcquisitionRepository(session)
        self._derived_repository = DerivedArtifactRepository(session)

    def ingest(
            self,
            *,
            document_version_id: int,
            content: bytes,
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
            additional_quality_limitations: Sequence[str] = (),
    ) -> TranscriptIngestionResult:
        version = self._session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        normalized_language = language.strip()
        if not _BCP47.fullmatch(normalized_language):
            raise ValueError(
                "language must be a simple valid BCP 47 language tag."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware.")
        for name, value in {
            "provider": provider,
            "provider_version": provider_version,
            "requested_location": requested_location,
            "media_type": media_type,
        }.items():
            if not value.strip():
                raise ValueError(f"{name} must not be blank.")
        text, normalization_limitations = _normalize_transcript(
            content, transcript_format
        )
        limitations = tuple(dict.fromkeys(
            (*normalization_limitations, *additional_quality_limitations)
        ))
        stored = self._artifact_store.store(content)
        raw_artifact = self._raw_repository.get_or_create(stored)
        acquisition = self._acquisition_repository.register(
            document_version=version,
            raw_artifact=raw_artifact,
            provider=provider,
            provider_version=provider_version,
            requested_location=requested_location,
            resolved_location=resolved_location,
            external_identifier=external_identifier,
            retrieved_at=retrieved_at,
            language=normalized_language,
            transcript_kind=transcript_kind,
            transcript_format=transcript_format,
            media_type=media_type,
        )
        artifact = self._derived_repository.register(
            document_version=version,
            artifact_type=DerivedArtifactType.TRANSCRIPT,
            method=self.METHOD,
            method_version=self.METHOD_VERSION,
            schema_version=self.SCHEMA_VERSION,
            payload={
                "text": text,
                "character_count": len(text),
                "language": normalized_language,
                "transcript_kind": transcript_kind.value,
                "transcript_format": transcript_format.value,
                "source": {
                    "transcript_acquisition_id": acquisition.id,
                    "raw_artifact_id": raw_artifact.id,
                    "hash_algorithm": raw_artifact.hash_algorithm,
                    "content_hash": raw_artifact.content_hash,
                },
            },
            quality_limitations=limitations,
        )
        return _result(
            version=version,
            acquisition=acquisition,
            artifact=artifact,
            raw_artifact_id=raw_artifact.id,
            raw_content_hash=raw_artifact.content_hash,
            text=text,
            language=normalized_language,
            transcript_kind=transcript_kind,
            transcript_format=transcript_format,
            limitations=limitations,
        )


def ingest_transcript_file(
        *,
        document_version_id: int,
        transcript_file: Path,
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
        additional_quality_limitations: Sequence[str] = (),
        session_factory: Callable[[], Session] = SessionLocal,
        artifact_store: RawArtifactStore | None = None,
) -> TranscriptIngestionResult:
    """Import exact provider output and commit one complete provenance chain."""

    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware.")
    content = read_transcript_file(transcript_file)
    store = artifact_store or FileSystemRawArtifactStore(
        RAW_ARTIFACT_DIRECTORY
    )
    with session_factory() as session:
        try:
            result = TranscriptIngestionService(
                session,
                artifact_store=store,
            ).ingest(
                document_version_id=document_version_id,
                content=content,
                provider=provider,
                provider_version=provider_version,
                requested_location=requested_location,
                resolved_location=resolved_location,
                external_identifier=external_identifier,
                retrieved_at=retrieved_at.astimezone(timezone.utc),
                language=language,
                transcript_kind=transcript_kind,
                transcript_format=transcript_format,
                media_type=media_type,
                additional_quality_limitations=additional_quality_limitations,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def read_transcript_file(path: Path) -> bytes:
    """Read one explicit transcript file without following directories."""

    if not path.is_file():
        raise ValueError(f"Transcript file does not exist: {path}.")
    content = path.read_bytes()
    if not content:
        raise ValueError("Transcript file must not be empty.")
    return content


def _normalize_transcript(
        content: bytes,
        transcript_format: TranscriptFormat,
) -> tuple[str, tuple[str, ...]]:
    if not content:
        raise ValueError("Transcript content must not be empty.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Transcript content must be UTF-8 encoded.") from error
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if transcript_format is TranscriptFormat.PLAIN_TEXT:
        text = decoded.strip()
        limitations = (
            "Plain-text input has no machine-verifiable cue timing.",
        )
    else:
        text = _caption_text(decoded, transcript_format)
        limitations = (
            "Cue timing is preserved only in the immutable raw artifact; "
            "the normalized analytical text omits timestamps.",
        )
    if not text:
        raise ValueError("Transcript normalization produced no text.")
    return text, limitations


def _caption_text(text: str, transcript_format: TranscriptFormat) -> str:
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues: list[str] = []
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.split("\n")]
        if transcript_format is TranscriptFormat.WEBVTT and index == 0:
            if lines and lines[0].startswith("WEBVTT"):
                lines = lines[1:]
        if not lines:
            continue
        if lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = next(
            (i for i, line in enumerate(lines) if _TIMING_LINE.match(line)),
            None,
        )
        if timing_index is None:
            continue
        cue_lines = lines[timing_index + 1:]
        cue = " ".join(
            _CUE_TAG.sub("", unescape(line)).strip()
            for line in cue_lines
            if line.strip()
        ).strip()
        if cue:
            cues.append(cue)
    return "\n\n".join(cues)


def _result(
        *,
        version: DocumentVersion,
        acquisition: TranscriptAcquisition,
        artifact: DerivedArtifact,
        raw_artifact_id: int,
        raw_content_hash: str,
        text: str,
        language: str,
        transcript_kind: TranscriptKind,
        transcript_format: TranscriptFormat,
        limitations: tuple[str, ...],
) -> TranscriptIngestionResult:
    if (
            version.id is None
            or acquisition.id is None
            or artifact.id is None
            or raw_artifact_id is None
    ):
        raise RuntimeError("Transcript ingestion did not persist identifiers.")
    return TranscriptIngestionResult(
        document_version_id=version.id,
        transcript_acquisition_id=acquisition.id,
        raw_artifact_id=raw_artifact_id,
        transcript_artifact_id=artifact.id,
        raw_content_hash=raw_content_hash,
        text_content_hash=artifact.content_hash,
        character_count=len(text),
        language=language,
        transcript_kind=transcript_kind,
        transcript_format=transcript_format,
        quality_limitations=limitations,
    )
