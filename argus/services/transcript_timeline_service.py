from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import re

from sqlalchemy.orm import Session

from argus.acquisition import ArtifactIntegrityError, RawArtifactStore
from argus.config import RAW_ARTIFACT_DIRECTORY
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact, DocumentVersion, RawArtifact
from argus.services.transcript_provenance_service import (
    transcript_provenance_issue,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.transcripts import canonicalize_transcript_source


@dataclass(frozen=True, slots=True)
class TranscriptCueTimelineItem:
    cue_index: int
    source_block_index: int
    source_text_hash: str
    start_ms: int
    end_ms: int
    gap_before_ms: int | None
    normalized_cue_text: str
    output_start_char: int | None
    output_end_char: int | None
    output_text: str
    removed_prefix_word_count: int
    removed_internal_overlap_word_count: int
    suppression_reason: str | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def contributes_output(self) -> bool:
        return self.output_start_char is not None


@dataclass(frozen=True, slots=True)
class TranscriptTimelineReport:
    document_version_id: int
    transcript_artifact_id: int
    transcript_acquisition_id: int
    raw_artifact_id: int
    character_count: int
    text_hash: str
    cue_provenance_schema_version: str
    time_unit: str
    items: tuple[TranscriptCueTimelineItem, ...]

    @property
    def contributing_cue_count(self) -> int:
        return sum(item.contributes_output for item in self.items)

    @property
    def suppressed_cue_count(self) -> int:
        return len(self.items) - self.contributing_cue_count


def inspect_transcript_timeline(
        *,
        document_version_id: int,
        transcript_artifact_id: int,
        session_factory: Callable[[], Session] = SessionLocal,
        artifact_store: RawArtifactStore | None = None,
) -> TranscriptTimelineReport:
    """Validate and expose cue-level normalized-text provenance."""

    with session_factory() as session:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        artifact = session.get(DerivedArtifact, transcript_artifact_id)
        if artifact is None:
            raise ValueError(
                "Transcript artifact does not exist: "
                f"{transcript_artifact_id}."
            )
        if artifact.document_version_id != version.id:
            raise ValueError(
                "Transcript artifact belongs to another document version."
            )
        if artifact.artifact_type is not DerivedArtifactType.TRANSCRIPT:
            raise ValueError("Selected artifact is not a transcript.")
        issue = transcript_provenance_issue(session, artifact)
        if issue is not None:
            raise ValueError(issue)
        source = artifact.payload.get("source")
        if not isinstance(source, Mapping):
            raise ValueError(
                "Transcript payload has no structured source provenance."
            )
        raw_artifact_id = _positive_integer(source.get("raw_artifact_id"))
        raw_artifact = (
            None
            if raw_artifact_id is None
            else session.get(RawArtifact, raw_artifact_id)
        )
        if raw_artifact is None:
            raise ValueError("Transcript source raw artifact is missing.")
        store = artifact_store or FileSystemRawArtifactStore(
            RAW_ARTIFACT_DIRECTORY
        )
        if raw_artifact.storage_backend != store.storage_backend:
            raise ValueError(
                "Transcript raw artifact uses an unsupported storage backend."
            )
        try:
            raw_content = store.read(raw_artifact.storage_key)
        except (ArtifactIntegrityError, OSError) as error:
            raise ValueError(
                "Transcript raw artifact could not be verified."
            ) from error
        if (
                raw_artifact.hash_algorithm != "sha256"
                or hashlib.sha256(raw_content).hexdigest()
                != raw_artifact.content_hash
                or len(raw_content) != raw_artifact.byte_size
        ):
            raise ValueError("Transcript raw artifact integrity is invalid.")
        canonical_source = canonicalize_transcript_source(raw_content)
        source_blocks = tuple(
            re.split(r"\n[ \t]*\n", canonical_source.strip())
        )
        return _timeline_report(artifact, source_blocks=source_blocks)


def _timeline_report(
        artifact: DerivedArtifact,
        *,
        source_blocks: Sequence[str],
) -> TranscriptTimelineReport:
    text = artifact.payload.get("text")
    character_count = artifact.payload.get("character_count")
    if (
            not isinstance(text, str)
            or not _is_integer(character_count)
            or character_count != len(text)
    ):
        raise ValueError("Transcript text payload is inconsistent.")
    normalization = artifact.payload.get("normalization")
    if not isinstance(normalization, Mapping):
        raise ValueError("Transcript has no structured normalization metadata.")
    cue_provenance = normalization.get("cue_provenance")
    if not isinstance(cue_provenance, Mapping):
        raise ValueError(
            "Transcript has no cue provenance; reingest it with transcript "
            "normalization version 6 or later."
        )
    schema_version = cue_provenance.get("schema_version")
    time_unit = cue_provenance.get("time_unit")
    source_locator = cue_provenance.get("source_locator")
    source_canonicalization = cue_provenance.get("source_canonicalization")
    if schema_version != "1":
        raise ValueError("Unsupported transcript cue provenance schema.")
    if time_unit != "milliseconds":
        raise ValueError("Unsupported transcript cue time unit.")
    if source_locator != "canonical_caption_block_index":
        raise ValueError("Unsupported transcript cue source locator.")
    if source_canonicalization != "utf8_sig_decode_and_lf_newlines":
        raise ValueError("Unsupported transcript source canonicalization.")
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if cue_provenance.get("normalized_text_hash") != text_hash:
        raise ValueError("Transcript cue provenance text hash is inconsistent.")
    raw_cues = cue_provenance.get("cues")
    if not isinstance(raw_cues, Sequence) or isinstance(raw_cues, str):
        raise ValueError("Transcript cue provenance has no valid cue list.")
    if normalization.get("cue_count") != len(raw_cues):
        raise ValueError("Transcript cue count conflicts with cue provenance.")

    items: list[TranscriptCueTimelineItem] = []
    previous_source_block_index = 0
    previous_output_end_ms: int | None = None
    expected_output_start_char = 0
    reconstructed_output: list[str] = []
    for expected_index, raw_cue in enumerate(raw_cues, start=1):
        if not isinstance(raw_cue, Mapping):
            raise ValueError("Transcript cue provenance contains an invalid cue.")
        item = _timeline_item(
            raw_cue,
            text=text,
            expected_index=expected_index,
            previous_source_block_index=previous_source_block_index,
            previous_output_end_ms=previous_output_end_ms,
            expected_output_start_char=expected_output_start_char,
            source_blocks=source_blocks,
        )
        items.append(item)
        previous_source_block_index = item.source_block_index
        if item.contributes_output:
            reconstructed_output.append(item.output_text)
            previous_output_end_ms = item.end_ms
            expected_output_start_char = item.output_end_char + 1
    if " ".join(reconstructed_output) != text:
        raise ValueError(
            "Transcript cue provenance does not reconstruct normalized text."
        )

    source = artifact.payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("Transcript payload has no structured source provenance.")
    acquisition_id = _positive_integer(source.get("transcript_acquisition_id"))
    raw_artifact_id = _positive_integer(source.get("raw_artifact_id"))
    if acquisition_id is None or raw_artifact_id is None:
        raise ValueError("Transcript source provenance has invalid identifiers.")
    return TranscriptTimelineReport(
        document_version_id=artifact.document_version_id,
        transcript_artifact_id=artifact.id,
        transcript_acquisition_id=acquisition_id,
        raw_artifact_id=raw_artifact_id,
        character_count=len(text),
        text_hash=text_hash,
        cue_provenance_schema_version=schema_version,
        time_unit=time_unit,
        items=tuple(items),
    )


def _timeline_item(
        raw_cue: Mapping[object, object],
        *,
        text: str,
        expected_index: int,
        previous_source_block_index: int,
        previous_output_end_ms: int | None,
        expected_output_start_char: int,
        source_blocks: Sequence[str],
) -> TranscriptCueTimelineItem:
    cue_index = _positive_integer(raw_cue.get("cue_index"))
    source_block_index = _positive_integer(
        raw_cue.get("source_block_index")
    )
    start_ms = _non_negative_integer(raw_cue.get("start_ms"))
    end_ms = _positive_integer(raw_cue.get("end_ms"))
    source_text_hash = raw_cue.get("source_text_hash")
    normalized_cue_text = raw_cue.get("normalized_cue_text")
    removed_prefix = _non_negative_integer(
        raw_cue.get("removed_prefix_word_count")
    )
    removed_internal = _non_negative_integer(
        raw_cue.get("removed_internal_overlap_word_count")
    )
    if cue_index != expected_index:
        raise ValueError("Transcript cue indexes are not contiguous.")
    if (
            source_block_index is None
            or source_block_index <= previous_source_block_index
    ):
        raise ValueError("Transcript source block indexes are not increasing.")
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        raise ValueError("Transcript cue timing is invalid.")
    if not _is_sha256(source_text_hash):
        raise ValueError("Transcript cue source hash is invalid.")
    if source_block_index > len(source_blocks):
        raise ValueError("Transcript cue source block does not exist.")
    actual_source_hash = hashlib.sha256(
        source_blocks[source_block_index - 1].encode("utf-8")
    ).hexdigest()
    if source_text_hash != actual_source_hash:
        raise ValueError("Transcript cue source block hash is inconsistent.")
    if not isinstance(normalized_cue_text, str) or not normalized_cue_text:
        raise ValueError("Transcript normalized cue text is invalid.")
    if removed_prefix is None or removed_internal is None:
        raise ValueError("Transcript cue overlap counts are invalid.")

    output_start = raw_cue.get("output_start_char")
    output_end = raw_cue.get("output_end_char")
    suppression_reason = raw_cue.get("suppression_reason")
    if suppression_reason not in (None, "technical_relay", "exact_overlap"):
        raise ValueError("Transcript cue suppression reason is invalid.")
    if output_start is None and output_end is None:
        if suppression_reason is None:
            raise ValueError("Suppressed transcript cue has no reason.")
        if removed_prefix > len(normalized_cue_text.split()):
            raise ValueError("Transcript cue removed-prefix count is invalid.")
        output_text = ""
        gap_before_ms = None
    else:
        if (
                not _is_integer(output_start)
                or not _is_integer(output_end)
                or output_start < 0
                or output_start != expected_output_start_char
                or output_end <= output_start
                or output_end > len(text)
                or suppression_reason is not None
        ):
            raise ValueError("Transcript cue output span is invalid.")
        output_text = text[output_start:output_end]
        cue_words = normalized_cue_text.split()
        if removed_prefix > len(cue_words):
            raise ValueError("Transcript cue removed-prefix count is invalid.")
        if output_text != " ".join(cue_words[removed_prefix:]):
            raise ValueError("Transcript cue output span conflicts with its text.")
        gap_before_ms = (
            None
            if previous_output_end_ms is None
            else start_ms - previous_output_end_ms
        )
    return TranscriptCueTimelineItem(
        cue_index=cue_index,
        source_block_index=source_block_index,
        source_text_hash=source_text_hash,
        start_ms=start_ms,
        end_ms=end_ms,
        gap_before_ms=gap_before_ms,
        normalized_cue_text=normalized_cue_text,
        output_start_char=output_start,
        output_end_char=output_end,
        output_text=output_text,
        removed_prefix_word_count=removed_prefix,
        removed_internal_overlap_word_count=removed_internal,
        suppression_reason=suppression_reason,
    )


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_integer(value: object) -> int | None:
    if not _is_integer(value) or value < 1:
        return None
    return value


def _non_negative_integer(value: object) -> int | None:
    if not _is_integer(value) or value < 0:
        return None
    return value


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
