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
    r"^\s*((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3})\s+-->\s+"
    r"((?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
)
_CUE_TAG = re.compile(r"<[^>]+>")
_INLINE_TIMESTAMP = re.compile(
    r"<(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}>"
)
_BCP47 = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_MIN_PARTIAL_OVERLAP_WORDS = 2
_ROLLING_BOUNDARY_TOLERANCE_MS = 50


@dataclass(frozen=True, slots=True)
class _CaptionCue:
    start_ms: int
    end_ms: int
    text: str
    rollup_prefix_word_count: int | None
    removed_internal_overlap_word_count: int


@dataclass(frozen=True, slots=True)
class _NormalizedTranscript:
    text: str
    limitations: tuple[str, ...]
    metadata: dict[str, object]


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
    METHOD_VERSION = "4"
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
        normalized = _normalize_transcript(
            content, transcript_format
        )
        limitations = tuple(dict.fromkeys(
            (*normalized.limitations, *additional_quality_limitations)
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
                "text": normalized.text,
                "character_count": len(normalized.text),
                "language": normalized_language,
                "transcript_kind": transcript_kind.value,
                "transcript_format": transcript_format.value,
                "normalization": normalized.metadata,
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
            text=normalized.text,
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
) -> _NormalizedTranscript:
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
        metadata = {
            "strategy": "plain-text-trim",
        }
    else:
        text, cue_count, removed_overlap_word_count = _caption_text(
            decoded, transcript_format
        )
        limitations = (
            "Cue timing and technical cue boundaries are preserved only in "
            "the immutable raw artifact; the normalized analytical text "
            "omits them.",
        )
        metadata = {
            "strategy": "timing-aware-caption-rollup",
            "cue_count": cue_count,
            "removed_overlap_word_count": removed_overlap_word_count,
        }
    if not text:
        raise ValueError("Transcript normalization produced no text.")
    return _NormalizedTranscript(
        text=text,
        limitations=limitations,
        metadata=metadata,
    )


def _caption_text(
        text: str,
        transcript_format: TranscriptFormat,
) -> tuple[str, int, int]:
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues: list[_CaptionCue] = []
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.split("\n")]
        if transcript_format is TranscriptFormat.WEBVTT and index == 0:
            if lines and lines[0].startswith("WEBVTT"):
                lines = lines[1:]
        if not lines:
            continue
        if lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = None
        timing_match = None
        for line_index, line in enumerate(lines):
            candidate = _TIMING_LINE.match(line)
            if candidate is not None:
                timing_index = line_index
                timing_match = candidate
                break
        if timing_index is None:
            continue
        cue_lines = lines[timing_index + 1:]
        (
            cue,
            rollup_prefix_word_count,
            removed_internal_overlap_word_count,
        ) = _normalized_caption_cue(
            cue_lines,
            transcript_format=transcript_format,
        )
        if cue:
            if timing_match is None:
                raise RuntimeError("Caption timing match was not retained.")
            cues.append(_CaptionCue(
                start_ms=_timestamp_ms(timing_match.group(1)),
                end_ms=_timestamp_ms(timing_match.group(2)),
                text=cue,
                rollup_prefix_word_count=rollup_prefix_word_count,
                removed_internal_overlap_word_count=(
                    removed_internal_overlap_word_count
                ),
            ))
    words: list[str] = []
    removed_overlap_word_count = 0
    previous: _CaptionCue | None = None
    for cue in cues:
        removed_overlap_word_count += (
            cue.removed_internal_overlap_word_count
        )
        cue_words = cue.text.split()
        overlap = 0
        if previous is not None and _may_roll_up(previous, cue):
            overlap_candidate = cue_words
            if cue.rollup_prefix_word_count is not None:
                overlap_candidate = cue_words[:cue.rollup_prefix_word_count]
            overlap = _exact_word_overlap(words, overlap_candidate)
            if (
                    overlap < _MIN_PARTIAL_OVERLAP_WORDS
                    and overlap != len(overlap_candidate)
            ):
                overlap = 0
        words.extend(cue_words[overlap:])
        removed_overlap_word_count += overlap
        previous = cue
    return " ".join(words), len(cues), removed_overlap_word_count


def _may_roll_up(previous: _CaptionCue, incoming: _CaptionCue) -> bool:
    if incoming.start_ms < previous.end_ms:
        return True
    return (
        incoming.rollup_prefix_word_count is not None
        and incoming.start_ms - previous.end_ms
        <= _ROLLING_BOUNDARY_TOLERANCE_MS
    )


def _normalized_caption_cue(
        cue_lines: Sequence[str],
        *,
        transcript_format: TranscriptFormat,
) -> tuple[str, int | None, int]:
    decoded_lines = [
        unescape(line).strip()
        for line in cue_lines
        if line.strip()
    ]
    raw_cue = " ".join(decoded_lines).strip()
    if transcript_format is not TranscriptFormat.WEBVTT:
        return _CUE_TAG.sub("", raw_cue).strip(), None, 0
    inline_timestamp = _INLINE_TIMESTAMP.search(raw_cue)
    if inline_timestamp is None:
        return _CUE_TAG.sub("", raw_cue).strip(), None, 0

    prefix_fragments: list[list[str]] = []
    timed_fragments: list[str] = []
    timestamp_seen = False
    for line in decoded_lines:
        if timestamp_seen:
            timed_fragments.append(line)
            continue
        timestamp = _INLINE_TIMESTAMP.search(line)
        if timestamp is None:
            prefix_fragments.append(_CUE_TAG.sub("", line).split())
            continue
        prefix_fragments.append(
            _CUE_TAG.sub("", line[:timestamp.start()]).split()
        )
        timed_fragments.append(line[timestamp.start():])
        timestamp_seen = True

    prefix_words, removed_internal_overlap_word_count = (
        _collapse_exact_line_rollup(prefix_fragments)
    )
    timed_words = _CUE_TAG.sub(
        "", " ".join(timed_fragments)
    ).split()
    cue_words = [*prefix_words, *timed_words]
    return (
        " ".join(cue_words),
        len(prefix_words),
        removed_internal_overlap_word_count,
    )


def _collapse_exact_line_rollup(
        fragments: Sequence[Sequence[str]],
) -> tuple[list[str], int]:
    words: list[str] = []
    removed_overlap_word_count = 0
    for fragment in fragments:
        incoming = list(fragment)
        if not incoming:
            continue
        overlap = _exact_word_overlap(words, incoming)
        if (
                overlap < _MIN_PARTIAL_OVERLAP_WORDS
                and overlap != len(incoming)
        ):
            overlap = 0
        words.extend(incoming[overlap:])
        removed_overlap_word_count += overlap
    return words, removed_overlap_word_count


def _timestamp_ms(value: str) -> int:
    fields = value.replace(",", ".").split(":")
    if len(fields) == 2:
        hours = 0
        minutes, seconds = fields
    elif len(fields) == 3:
        hours, minutes, seconds = fields
    else:
        raise ValueError(f"Unsupported caption timestamp: {value!r}.")
    whole_seconds, milliseconds = seconds.split(".")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(whole_seconds) * 1_000
        + int(milliseconds)
    )


def _exact_word_overlap(existing: list[str], incoming: list[str]) -> int:
    maximum = min(len(existing), len(incoming))
    for size in range(maximum, 0, -1):
        if existing[-size:] == incoming[:size]:
            return size
    return 0


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
