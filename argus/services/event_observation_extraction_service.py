from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.analysis.spacy_event_observation_extractor import (
    SpacyEventObservationExtractor,
)
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.event_observations import (
    EventObservationExtractor,
    EventObservationType,
    ExtractedEventObservation,
)
from argus.models import (
    DerivedArtifact,
    Document,
    DocumentVersion,
    EventFragmentCandidate,
    EventObservationCandidate,
)
from argus.services.event_fragment_service import SUPPORTED_TEXT_TYPES
from argus.storage.derived_artifact_repository import (
    DerivedArtifactRepository,
)
from argus.storage.event_fragment_repository import EventFragmentRepository
from argus.storage.event_observation_repository import (
    EventObservationRepository,
)


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class EventObservationView:
    event_observation_id: int | None
    event_fragment_id: int
    observation_type: EventObservationType
    source_label: str
    surface_text: str
    normalized_value: str
    start_char: int
    end_char: int
    rationale: str


@dataclass(frozen=True, slots=True)
class EventObservationExtractionReport:
    document_version_id: int
    text_derived_artifact_id: int
    event_observation_artifact_id: int | None
    fragment_method: str
    fragment_method_version: str
    extraction_method: str
    extraction_method_version: str
    persisted: bool
    items: tuple[EventObservationView, ...]
    quality_limitations: tuple[str, ...]
    fragment_ids: tuple[int, ...]

    @property
    def fragment_count(self) -> int:
        return len(self.fragment_ids)

    @property
    def observation_count(self) -> int:
        return len(self.items)

    def count(self, observation_type: EventObservationType) -> int:
        return sum(
            item.observation_type is observation_type for item in self.items
        )


def extract_event_observations(
        *,
        document_version_id: int,
        text_derived_artifact_id: int | None = None,
        fragment_method: str | None = None,
        fragment_method_version: str | None = None,
        persist: bool = False,
        extractor: EventObservationExtractor | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EventObservationExtractionReport:
    """Extract model signals from one explicit persisted fragment set."""

    if (fragment_method is None) != (fragment_method_version is None):
        raise ValueError(
            "fragment_method and fragment_method_version must be supplied "
            "together."
        )
    selected_extractor = extractor or SpacyEventObservationExtractor()
    with session_factory() as session:
        version, document, artifact, text = _load_source(
            session,
            document_version_id=document_version_id,
            text_derived_artifact_id=text_derived_artifact_id,
        )
        fragments, selected_fragment_method, selected_fragment_version = (
            _select_fragments(
                session,
                document_version_id=document_version_id,
                text_derived_artifact_id=artifact.id,
                fragment_method=fragment_method,
                fragment_method_version=fragment_method_version,
            )
        )
        language = _language(document)
        method_version = selected_extractor.method_version(language)
        extracted_by_fragment: list[
            tuple[EventFragmentCandidate, tuple[ExtractedEventObservation, ...]]
        ] = []
        limitations: list[str] = []

        for fragment in fragments:
            fragment_text = text[fragment.start_char:fragment.end_char]
            result = selected_extractor.extract(
                fragment_text,
                language=language,
            )
            _validate_observations(fragment_text, result.observations)
            extracted_by_fragment.append((fragment, result.observations))
            limitations.extend(result.quality_limitations)
            limitations.extend(fragment.quality_limitations)

        normalized_limitations = _unique((
            "Event-fragment boundaries are candidates, not event assignments.",
            (
                "Extracted observations describe source text, not verified "
                "real-world facts."
            ),
            *limitations,
        ))
        payload = _payload(
            text_artifact=artifact,
            fragment_method=selected_fragment_method,
            fragment_method_version=selected_fragment_version,
            fragments=extracted_by_fragment,
        )
        observation_artifact = None
        stored_rows: dict[tuple[int, int, int, str], EventObservationCandidate] = {}
        try:
            if persist:
                observation_artifact = DerivedArtifactRepository(
                    session
                ).register(
                    document_version=version,
                    artifact_type=DerivedArtifactType.EVENT_OBSERVATIONS,
                    method=selected_extractor.method,
                    method_version=method_version,
                    schema_version=SCHEMA_VERSION,
                    payload=payload,
                    quality_limitations=normalized_limitations,
                )
                repository = EventObservationRepository(session)
                for fragment, observations in extracted_by_fragment:
                    rows = repository.register(
                        artifact=observation_artifact,
                        fragment=fragment,
                        observations=observations,
                    )
                    for row in rows:
                        stored_rows[_row_key(row)] = row
                session.commit()
        except Exception:
            session.rollback()
            raise

        items: list[EventObservationView] = []
        for fragment, observations in extracted_by_fragment:
            for item in observations:
                absolute_start = fragment.start_char + item.start_char
                absolute_end = fragment.start_char + item.end_char
                row = stored_rows.get((
                    fragment.id,
                    absolute_start,
                    absolute_end,
                    f"{item.observation_type.value}:{item.source_label}",
                ))
                items.append(EventObservationView(
                    event_observation_id=None if row is None else row.id,
                    event_fragment_id=fragment.id,
                    observation_type=item.observation_type,
                    source_label=item.source_label,
                    surface_text=item.surface_text,
                    normalized_value=item.normalized_value,
                    start_char=absolute_start,
                    end_char=absolute_end,
                    rationale=item.rationale,
                ))
        return EventObservationExtractionReport(
            document_version_id=document_version_id,
            text_derived_artifact_id=artifact.id,
            event_observation_artifact_id=(
                None if observation_artifact is None
                else observation_artifact.id
            ),
            fragment_method=selected_fragment_method,
            fragment_method_version=selected_fragment_version,
            extraction_method=selected_extractor.method,
            extraction_method_version=method_version,
            persisted=persist,
            items=tuple(items),
            quality_limitations=normalized_limitations,
            fragment_ids=tuple(fragment.id for fragment in fragments),
        )


def _load_source(
        session: Session,
        *,
        document_version_id: int,
        text_derived_artifact_id: int | None,
) -> tuple[DocumentVersion, Document, DerivedArtifact, str]:
    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(
            f"Document version does not exist: {document_version_id}."
        )
    document = session.get(Document, version.document_id)
    if document is None:
        raise ValueError("Document version references a missing document.")
    supported = [
        item for item in DerivedArtifactRepository(session).get_for_version(
            document_version_id
        )
        if item.artifact_type in SUPPORTED_TEXT_TYPES
    ]
    if text_derived_artifact_id is None:
        if not supported:
            raise ValueError("Document version has no supported text artifact.")
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
                "Event observations require a supported text artifact."
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
    return version, document, artifact, text


def _select_fragments(
        session: Session,
        *,
        document_version_id: int,
        text_derived_artifact_id: int,
        fragment_method: str | None,
        fragment_method_version: str | None,
) -> tuple[list[EventFragmentCandidate], str, str]:
    candidates = [
        item for item in EventFragmentRepository(
            session
        ).get_for_document_version(document_version_id)
        if item.text_derived_artifact_id == text_derived_artifact_id
    ]
    if not candidates:
        raise ValueError(
            "No persisted event fragments exist for the selected text "
            "artifact."
        )
    groups: dict[tuple[str, str], list[EventFragmentCandidate]] = {}
    for item in candidates:
        groups.setdefault((item.method, item.method_version), []).append(item)
    if fragment_method is None:
        if len(groups) != 1:
            choices = ", ".join(
                f"{method}@{version}" for method, version in sorted(groups)
            )
            raise ValueError(
                "Several fragment methods exist; choose "
                "--fragment-method and --fragment-method-version from: "
                f"{choices}."
            )
        selected_key = next(iter(groups))
    else:
        selected_key = (fragment_method.strip(), fragment_method_version.strip())
        if not all(selected_key):
            raise ValueError("Fragment method values must not be blank.")
        if selected_key not in groups:
            raise ValueError(
                "No persisted event fragments match the selected method "
                "and version."
            )
    return groups[selected_key], selected_key[0], selected_key[1]


def _language(document: Document) -> str:
    if not document.language or not document.language.strip():
        raise ValueError("Event observations require a document language.")
    return document.language.strip().lower().split("-", maxsplit=1)[0]


def _validate_observations(
        text: str,
        observations: Sequence[ExtractedEventObservation],
) -> None:
    previous_key: tuple[int, int, str, str] | None = None
    seen: set[tuple[int, int, str, str]] = set()
    for item in observations:
        if item.end_char > len(text):
            raise ValueError("Event observation exceeds its fragment.")
        if text[item.start_char:item.end_char] != item.surface_text:
            raise ValueError(
                "Event observation surface text does not match its offsets."
            )
        key = (
            item.start_char,
            item.end_char,
            item.observation_type.value,
            item.source_label,
        )
        if key in seen:
            raise ValueError("Event observations contain a duplicate origin.")
        if previous_key is not None and key < previous_key:
            raise ValueError("Event observations must be source ordered.")
        seen.add(key)
        previous_key = key


def _payload(
        *,
        text_artifact: DerivedArtifact,
        fragment_method: str,
        fragment_method_version: str,
        fragments: Sequence[
            tuple[EventFragmentCandidate, Sequence[ExtractedEventObservation]]
        ],
) -> dict[str, object]:
    return {
        "text_artifact_id": text_artifact.id,
        "text_content_hash": text_artifact.content_hash,
        "fragment_method": fragment_method,
        "fragment_method_version": fragment_method_version,
        "fragments": [
            {
                "event_fragment_id": fragment.id,
                "start_char": fragment.start_char,
                "end_char": fragment.end_char,
                "text_hash": fragment.text_hash,
            }
            for fragment, _ in fragments
        ],
        "observations": [
            {
                "event_fragment_id": fragment.id,
                "observation_type": item.observation_type.value,
                "source_label": item.source_label,
                "surface_text": item.surface_text,
                "normalized_value": item.normalized_value,
                "start_char": fragment.start_char + item.start_char,
                "end_char": fragment.start_char + item.end_char,
                "rationale": item.rationale,
            }
            for fragment, observations in fragments
            for item in observations
        ],
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _row_key(row: EventObservationCandidate) -> tuple[int, int, int, str]:
    return (
        row.event_fragment_candidate_id,
        row.start_char,
        row.end_char,
        f"{row.observation_type.value}:{row.source_label}",
    )
