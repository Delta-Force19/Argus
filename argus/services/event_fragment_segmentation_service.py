from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import re

from sqlalchemy.orm import Session

from argus.acquisition import RawArtifactStore
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact, Document, DocumentVersion
from argus.services.event_text_readiness_service import (
    EventTextReadiness,
    EventTextReadinessStatus,
    assess_event_text_readiness,
)
from argus.services.event_fragment_service import (
    SUPPORTED_TEXT_TYPES,
    register_event_fragment_candidate,
)
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.services.transcript_provenance_service import (
    transcript_provenance_issue,
)
from argus.services.transcript_timeline_service import (
    TranscriptTimelineReport,
    inspect_transcript_timeline,
)


METHOD = "deterministic-heading-paragraph-segmentation"
METHOD_VERSION = "1"
TRANSCRIPT_METHOD = "deterministic-cue-gap-segmentation"
TRANSCRIPT_METHOD_VERSION = "1"
CREATED_BY = "argus"
_TRANSCRIPT_BOUNDARY_GAP_MS = 2200
_BLANK_LINE = re.compile(r"(?:\r?\n[ \t]*){2,}")
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TextBlockView:
    block_index: int
    start_char: int
    end_char: int
    text_hash: str
    text: str
    heading_candidate: bool


@dataclass(frozen=True, slots=True)
class DocumentTextInspection:
    document_version_id: int
    text_derived_artifact_id: int
    character_count: int
    text_hash: str
    blocks: tuple[TextBlockView, ...]
    event_text_readiness: EventTextReadiness


@dataclass(frozen=True, slots=True)
class EventFragmentProposal:
    event_fragment_id: int | None
    start_char: int
    end_char: int
    text_hash: str
    rationale: str
    quality_limitations: tuple[str, ...]
    start_cue_index: int | None = None
    start_ms: int | None = None
    gap_before_ms: int | None = None


@dataclass(frozen=True, slots=True)
class EventFragmentSegmentationReport:
    document_version_id: int
    text_derived_artifact_id: int
    method: str
    method_version: str
    persisted: bool
    boundary_basis: str
    items: tuple[EventFragmentProposal, ...]
    event_text_readiness: EventTextReadiness

    @property
    def fragment_count(self) -> int:
        return len(self.items)


def inspect_document_text(
        *,
        document_version_id: int,
        text_derived_artifact_id: int | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentTextInspection:
    """Expose exact paragraph-block offsets without changing stored state."""

    with session_factory() as session:
        document, artifact, text = _select_text_source(
            session,
            document_version_id=document_version_id,
            text_derived_artifact_id=text_derived_artifact_id,
        )
        blocks = tuple(
            TextBlockView(
                block_index=index,
                start_char=start,
                end_char=end,
                text_hash=_hash(text[start:end]),
                text=text[start:end],
                heading_candidate=_is_heading_candidate(text[start:end]),
            )
            for index, (start, end) in enumerate(_paragraph_spans(text), start=1)
        )
        return DocumentTextInspection(
            document_version_id=document_version_id,
            text_derived_artifact_id=artifact.id,
            character_count=len(text),
            text_hash=_hash(text),
            blocks=blocks,
            event_text_readiness=_readiness(session, document, artifact, text),
        )


def segment_event_fragments(
        *,
        document_version_id: int,
        text_derived_artifact_id: int | None = None,
        persist: bool = False,
        session_factory: Callable[[], Session] = SessionLocal,
        artifact_store: RawArtifactStore | None = None,
) -> EventFragmentSegmentationReport:
    """Propose conservative structural or cue-timed spans."""

    with session_factory() as session:
        document, artifact, text = _select_text_source(
            session,
            document_version_id=document_version_id,
            text_derived_artifact_id=text_derived_artifact_id,
        )
        readiness = _readiness(session, document, artifact, text)
        if persist and not readiness.ready_for_event_analysis:
            raise ValueError(
                "Event fragment persistence is blocked: "
                + " ".join(readiness.reasons)
            )
        timeline = None
        if _has_cue_provenance(artifact):
            timeline = inspect_transcript_timeline(
                document_version_id=document_version_id,
                transcript_artifact_id=artifact.id,
                session_factory=session_factory,
                artifact_store=artifact_store,
            )
        spans, boundary_basis = _propose_spans(text, timeline=timeline)
        method, method_version = _method_for(boundary_basis)
        limitations = (
            _limitations(boundary_basis)
            + readiness.reasons
            + readiness.limitations
        )
        timing_by_start = (
            {}
            if timeline is None
            else {
                item.output_start_char: item
                for item in timeline.items
                if item.contributes_output
            }
        )
        items: list[EventFragmentProposal] = []
        try:
            for index, (start, end) in enumerate(spans, start=1):
                boundary_cue = timing_by_start.get(start)
                timing_rationale = ""
                if boundary_cue is not None:
                    timing_rationale = (
                        f" start_cue={boundary_cue.cue_index}; "
                        f"start_ms={boundary_cue.start_ms}; "
                        "gap_before_ms="
                        f"{boundary_cue.gap_before_ms}."
                    )
                rationale = (
                    f"Deterministic fragment candidate {index}/{len(spans)}; "
                    f"boundary_basis={boundary_basis}."
                    f"{timing_rationale}"
                )
                event_fragment_id = None
                if persist:
                    stored = register_event_fragment_candidate(
                        session,
                        document_version_id=document_version_id,
                        text_derived_artifact_id=artifact.id,
                        start_char=start,
                        end_char=end,
                        method=method,
                        method_version=method_version,
                        created_by=CREATED_BY,
                        rationale=rationale,
                        quality_limitations=limitations,
                    )
                    event_fragment_id = stored.event_fragment_id
                items.append(
                    EventFragmentProposal(
                        event_fragment_id=event_fragment_id,
                        start_char=start,
                        end_char=end,
                        text_hash=_hash(text[start:end]),
                        rationale=rationale,
                        quality_limitations=limitations,
                        start_cue_index=(
                            None if boundary_cue is None
                            else boundary_cue.cue_index
                        ),
                        start_ms=(
                            None if boundary_cue is None
                            else boundary_cue.start_ms
                        ),
                        gap_before_ms=(
                            None if boundary_cue is None
                            else boundary_cue.gap_before_ms
                        ),
                    )
                )
            if persist:
                session.commit()
        except Exception:
            session.rollback()
            raise
        return EventFragmentSegmentationReport(
            document_version_id=document_version_id,
            text_derived_artifact_id=artifact.id,
            method=method,
            method_version=method_version,
            persisted=persist,
            boundary_basis=boundary_basis,
            items=tuple(items),
            event_text_readiness=readiness,
        )


def _select_text_source(
        session: Session,
        *,
        document_version_id: int,
        text_derived_artifact_id: int | None,
) -> tuple[Document, DerivedArtifact, str]:
    artifacts = DerivedArtifactRepository(session).get_for_version(
        document_version_id
    )
    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(
            f"Document version does not exist: {document_version_id}."
        )
    document = session.get(Document, version.document_id)
    if document is None:
        raise ValueError("Document version references a missing document.")
    supported = [
        item for item in artifacts if item.artifact_type in SUPPORTED_TEXT_TYPES
    ]
    if text_derived_artifact_id is None:
        if not supported:
            raise ValueError(
                "Document version has no supported text derived artifact: "
                f"{document_version_id}."
            )
        if len(supported) != 1:
            identifiers = ",".join(str(item.id) for item in supported)
            raise ValueError(
                "Document version has multiple supported text artifacts; "
                f"choose --text-artifact-id from: {identifiers}."
            )
        artifact = supported[0]
    else:
        artifact = session.get(DerivedArtifact, text_derived_artifact_id)
        if artifact is None:
            raise ValueError(
                "Derived text artifact does not exist: "
                f"{text_derived_artifact_id}."
            )
        if artifact.document_version_id != document_version_id:
            raise ValueError(
                "Derived text artifact belongs to another document version."
            )
        if artifact.artifact_type not in SUPPORTED_TEXT_TYPES:
            raise ValueError(
                "Event segmentation requires a supported text derived artifact."
            )
    text = artifact.payload.get("text")
    character_count = artifact.payload.get("character_count")
    if (
            not isinstance(text, str)
            or not isinstance(character_count, int)
            or isinstance(character_count, bool)
            or character_count != len(text)
    ):
        raise ValueError("Derived text payload is inconsistent.")
    return document, artifact, text


def _readiness(
        session: Session,
        document: Document,
        artifact: DerivedArtifact,
        text: str,
) -> EventTextReadiness:
    readiness = assess_event_text_readiness(
        identifier_scheme=document.identifier_scheme,
        identifier_value=document.identifier_value,
        artifact_type=artifact.artifact_type,
        text=text,
    )
    issue = transcript_provenance_issue(session, artifact)
    if issue is None:
        return readiness
    return EventTextReadiness(
        status=EventTextReadinessStatus.BLOCKED,
        ready_for_event_analysis=False,
        reasons=readiness.reasons + (issue,),
        limitations=readiness.limitations,
    )


def _paragraph_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for separator in _BLANK_LINE.finditer(text):
        span = _trimmed_span(text, cursor, separator.start())
        if span is not None:
            spans.append(span)
        cursor = separator.end()
    span = _trimmed_span(text, cursor, len(text))
    if span is not None:
        spans.append(span)
    return tuple(spans)


def _propose_spans(
        text: str,
        *,
        timeline: TranscriptTimelineReport | None = None,
) -> tuple[tuple[tuple[int, int], ...], str]:
    if timeline is not None:
        return _cue_timed_spans(text, timeline)
    blocks = _paragraph_spans(text)
    if not blocks:
        raise ValueError("Derived text does not contain non-whitespace content.")
    heading_indexes = [
        index
        for index, (start, end) in enumerate(blocks)
        if _is_heading_candidate(text[start:end])
    ]
    content_start = blocks[0][0]
    content_end = blocks[-1][1]
    starts = [content_start]
    ignored_first_section_heading = (
        len(heading_indexes) >= 2
        and heading_indexes[0] == 0
        and heading_indexes[1] == 1
    )
    for position, block_index in enumerate(heading_indexes):
        if block_index == 0:
            continue
        if ignored_first_section_heading and position == 1:
            continue
        starts.append(blocks[block_index][0])
    starts = sorted(set(starts))
    if len(starts) < 2:
        return ((content_start, content_end),), "whole-content-fallback"
    spans = []
    for index, start in enumerate(starts):
        raw_end = starts[index + 1] if index + 1 < len(starts) else content_end
        span = _trimmed_span(text, start, raw_end)
        if span is not None:
            spans.append(span)
    return tuple(spans), "heading-like-paragraphs"


def _cue_timed_spans(
        text: str,
        timeline: TranscriptTimelineReport,
) -> tuple[tuple[tuple[int, int], ...], str]:
    contributing = [item for item in timeline.items if item.contributes_output]
    if not contributing:
        raise ValueError("Transcript cue provenance contains no text output.")
    starts = [contributing[0].output_start_char]
    starts.extend(
        item.output_start_char
        for item in contributing[1:]
        if item.gap_before_ms is not None
        and item.gap_before_ms >= _TRANSCRIPT_BOUNDARY_GAP_MS
    )
    spans: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        raw_end = starts[index + 1] if index + 1 < len(starts) else len(text)
        span = _trimmed_span(text, start, raw_end)
        if span is not None:
            spans.append(span)
    return tuple(spans), f"cue-output-gap-at-least-{_TRANSCRIPT_BOUNDARY_GAP_MS}ms"


def _has_cue_provenance(artifact: DerivedArtifact) -> bool:
    if artifact.artifact_type is not DerivedArtifactType.TRANSCRIPT:
        return False
    normalization = artifact.payload.get("normalization")
    return (
        isinstance(normalization, dict)
        and "cue_provenance" in normalization
    )


def _method_for(boundary_basis: str) -> tuple[str, str]:
    if boundary_basis.startswith("cue-output-gap-at-least-"):
        return TRANSCRIPT_METHOD, TRANSCRIPT_METHOD_VERSION
    return METHOD, METHOD_VERSION


def _trimmed_span(
        text: str,
        start: int,
        end: int,
) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return None if start == end else (start, end)


def _is_heading_candidate(value: str) -> bool:
    normalized = " ".join(value.split())
    if not normalized or "\n" in value.strip(" \t\r\n"):
        return False
    words = _WORD.findall(normalized)
    if not 1 <= len(words) <= 18 or len(normalized) > 180:
        return False
    if normalized.endswith((".", "!", ";")):
        return False
    if normalized.endswith(":") and len(normalized) > 80:
        return False
    return True


def _limitations(boundary_basis: str) -> tuple[str, ...]:
    common = (
        "Structural boundaries are not proof that a span describes one event.",
        "Headings and paragraph separators may be missing or extracted incorrectly.",
    )
    if boundary_basis.startswith("cue-output-gap-at-least-"):
        return common + (
            "Cue timing gaps are boundary proposals, not proof that adjacent "
            "spans describe different events.",
            "Editorial transitions without a qualifying timing gap remain "
            "inside one provisional fragment.",
        )
    if boundary_basis == "whole-content-fallback":
        return common + (
            "No repeatable internal heading boundary was detected; the whole "
            "non-whitespace text is one provisional candidate.",
        )
    return common


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
