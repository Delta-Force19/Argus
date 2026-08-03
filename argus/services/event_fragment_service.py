from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.models import (
    DerivedArtifact,
    DocumentVersion,
    EventFragmentCandidate,
)
from argus.storage.event_fragment_repository import EventFragmentRepository


SUPPORTED_TEXT_TYPES = frozenset({
    DerivedArtifactType.EXTRACTED_TEXT,
    DerivedArtifactType.OCR_TEXT,
    DerivedArtifactType.TRANSCRIPT,
    DerivedArtifactType.TRANSLATION,
})


@dataclass(frozen=True, slots=True)
class EventFragmentView:
    event_fragment_id: int
    document_version_id: int
    text_derived_artifact_id: int
    start_char: int
    end_char: int
    text_hash: str
    method: str
    method_version: str
    created_by: str
    rationale: str
    quality_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventFragmentReport:
    document_version_id: int
    items: tuple[EventFragmentView, ...]
    event_assignment_count: int = 0

    @property
    def event_fragment_count(self) -> int:
        return len(self.items)


def register_event_fragment_candidate(
        session: Session,
        *,
        document_version_id: int,
        text_derived_artifact_id: int,
        start_char: int,
        end_char: int,
        method: str,
        method_version: str,
        created_by: str,
        rationale: str,
        quality_limitations: Sequence[str] = (),
) -> EventFragmentView:
    """Register one immutable candidate span; transaction stays caller-owned."""

    version, artifact, text = _load_source(
        session,
        document_version_id=document_version_id,
        text_derived_artifact_id=text_derived_artifact_id,
    )
    del version
    _validate_span(start_char=start_char, end_char=end_char, text=text)
    normalized_method = _required(method, "method")
    normalized_method_version = _required(method_version, "method_version")
    normalized_created_by = _required(created_by, "created_by")
    normalized_rationale = _required(rationale, "rationale")
    normalized_limitations = _limitations(quality_limitations)
    text_hash = _hash(text[start_char:end_char])

    repository = EventFragmentRepository(session)
    existing = repository.get_origin(
        text_derived_artifact_id=artifact.id,
        start_char=start_char,
        end_char=end_char,
        method=normalized_method,
        method_version=normalized_method_version,
    )
    if existing is not None:
        expected = (
            document_version_id,
            text_hash,
            normalized_created_by,
            normalized_rationale,
            normalized_limitations,
        )
        actual = (
            existing.document_version_id,
            existing.text_hash,
            existing.created_by,
            existing.rationale,
            tuple(existing.quality_limitations),
        )
        if actual != expected:
            raise ValueError(
                "Event fragment origin already exists with conflicting "
                "provenance."
            )
        return _view(existing)

    candidate = EventFragmentCandidate(
        document_version_id=document_version_id,
        text_derived_artifact_id=artifact.id,
        start_char=start_char,
        end_char=end_char,
        text_hash=text_hash,
        method=normalized_method,
        method_version=normalized_method_version,
        created_by=normalized_created_by,
        rationale=normalized_rationale,
        quality_limitations=list(normalized_limitations),
    )
    repository.add(candidate)
    repository.flush()
    return _view(candidate)


def get_event_fragments(
        *,
        document_version_id: int,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EventFragmentReport:
    """Read and revalidate all candidates for an existing document version."""

    with session_factory() as session:
        if session.get(DocumentVersion, document_version_id) is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        repository = EventFragmentRepository(session)
        candidates = repository.get_for_document_version(document_version_id)
        items: list[EventFragmentView] = []
        for candidate in candidates:
            _, _, text = _load_source(
                session,
                document_version_id=document_version_id,
                text_derived_artifact_id=(
                    candidate.text_derived_artifact_id
                ),
            )
            _validate_span(
                start_char=candidate.start_char,
                end_char=candidate.end_char,
                text=text,
            )
            if _hash(text[candidate.start_char:candidate.end_char]) != (
                    candidate.text_hash
            ):
                raise ValueError(
                    "Event fragment text hash does not match its source span: "
                    f"event_fragment_id={candidate.id}."
                )
            items.append(_view(candidate))
        return EventFragmentReport(
            document_version_id=document_version_id,
            items=tuple(items),
        )


def _load_source(
        session: Session,
        *,
        document_version_id: int,
        text_derived_artifact_id: int,
) -> tuple[DocumentVersion, DerivedArtifact, str]:
    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(
            f"Document version does not exist: {document_version_id}."
        )
    artifact = session.get(DerivedArtifact, text_derived_artifact_id)
    if artifact is None:
        raise ValueError(
            f"Derived text artifact does not exist: {text_derived_artifact_id}."
        )
    if artifact.document_version_id != version.id:
        raise ValueError(
            "Derived text artifact belongs to another document version."
        )
    if artifact.artifact_type not in SUPPORTED_TEXT_TYPES:
        raise ValueError(
            "Event fragments require a supported text derived artifact."
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
    return version, artifact, text


def _validate_span(*, start_char: int, end_char: int, text: str) -> None:
    if isinstance(start_char, bool) or isinstance(end_char, bool):
        raise ValueError("Event fragment offsets must be integers.")
    if start_char < 0 or end_char <= start_char or end_char > len(text):
        raise ValueError(
            "Event fragment span must be a non-empty range inside the text."
        )


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank.")
    return normalized


def _limitations(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("quality_limitations must not contain blanks.")
    return normalized


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _view(candidate: EventFragmentCandidate) -> EventFragmentView:
    assert candidate.id is not None
    return EventFragmentView(
        event_fragment_id=candidate.id,
        document_version_id=candidate.document_version_id,
        text_derived_artifact_id=candidate.text_derived_artifact_id,
        start_char=candidate.start_char,
        end_char=candidate.end_char,
        text_hash=candidate.text_hash,
        method=candidate.method,
        method_version=candidate.method_version,
        created_by=candidate.created_by,
        rationale=candidate.rationale,
        quality_limitations=tuple(candidate.quality_limitations),
    )
