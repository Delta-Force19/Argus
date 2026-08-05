from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.analysis.deterministic_event_fragment_profiler import (
    DeterministicEventFragmentProfiler,
)
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.event_fragment_profiles import (
    EventFragmentProfileExclusion,
    EventFragmentProfiler,
    EventFragmentProfileSignal,
    ProfileExclusionReason,
    ProfileObservation,
)
from argus.event_observations import EventObservationType
from argus.models import DerivedArtifact, Document, DocumentVersion
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.event_observation_repository import (
    EventObservationRepository,
)


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class EventFragmentProfileView:
    event_fragment_id: int
    signals: tuple[EventFragmentProfileSignal, ...]
    exclusions: tuple[EventFragmentProfileExclusion, ...]

    @property
    def retained_occurrence_count(self) -> int:
        return sum(item.occurrence_count for item in self.signals)

    def signal_count(self, observation_type: EventObservationType) -> int:
        return sum(
            item.observation_type is observation_type for item in self.signals
        )

    def exclusion_count(self, reason: ProfileExclusionReason) -> int:
        return sum(item.reason is reason for item in self.exclusions)


@dataclass(frozen=True, slots=True)
class EventFragmentProfileReport:
    document_version_id: int
    event_observation_artifact_id: int
    event_fragment_profile_artifact_id: int | None
    profile_method: str
    profile_method_version: str
    persisted: bool
    profiles: tuple[EventFragmentProfileView, ...]
    quality_limitations: tuple[str, ...]

    @property
    def raw_observation_count(self) -> int:
        return sum(
            profile.retained_occurrence_count + len(profile.exclusions)
            for profile in self.profiles
        )

    @property
    def retained_occurrence_count(self) -> int:
        return sum(
            profile.retained_occurrence_count for profile in self.profiles
        )

    @property
    def signal_count(self) -> int:
        return sum(len(profile.signals) for profile in self.profiles)

    @property
    def exclusion_count(self) -> int:
        return sum(len(profile.exclusions) for profile in self.profiles)


def profile_event_fragments(
        *,
        document_version_id: int,
        event_observation_artifact_id: int | None = None,
        persist: bool = False,
        profiler: EventFragmentProfiler | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EventFragmentProfileReport:
    """Build transparent profiles from one exact observation artifact."""

    selected_profiler = profiler or DeterministicEventFragmentProfiler()
    with session_factory() as session:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        document = session.get(Document, version.document_id)
        if document is None:
            raise ValueError("Document version references a missing document.")
        if not document.language or not document.language.strip():
            raise ValueError("Fragment profiles require a document language.")
        language = document.language.strip().lower().split("-", maxsplit=1)[0]
        observation_artifact = _select_observation_artifact(
            session,
            document_version_id=document_version_id,
            artifact_id=event_observation_artifact_id,
        )
        rows = EventObservationRepository(session).get_for_artifact(
            observation_artifact.id
        )
        if not rows:
            raise ValueError(
                "Selected event-observation artifact has no projected rows."
            )
        _validate_projection(observation_artifact, rows)
        grouped: dict[int, list[ProfileObservation]] = defaultdict(list)
        for row in rows:
            grouped[row.event_fragment_candidate_id].append(ProfileObservation(
                observation_id=row.id,
                observation_type=row.observation_type,
                source_label=row.source_label,
                surface_text=row.surface_text,
                normalized_value=row.normalized_value,
                start_char=row.start_char,
                end_char=row.end_char,
            ))

        profiles: list[EventFragmentProfileView] = []
        limitations: list[str] = []
        for fragment_id, observations in sorted(grouped.items()):
            result = selected_profiler.profile(
                tuple(observations),
                language=language,
            )
            _validate_profile(observations, result.signals, result.exclusions)
            profiles.append(EventFragmentProfileView(
                event_fragment_id=fragment_id,
                signals=result.signals,
                exclusions=result.exclusions,
            ))
            limitations.extend(result.quality_limitations)
        limitations.extend(observation_artifact.quality_limitations)
        normalized_limitations = _unique(limitations)
        payload = _payload(
            observation_artifact=observation_artifact,
            profiles=profiles,
        )
        profile_artifact = None
        try:
            if persist:
                profile_artifact = DerivedArtifactRepository(session).register(
                    document_version=version,
                    artifact_type=DerivedArtifactType.EVENT_FRAGMENT_PROFILES,
                    method=selected_profiler.method,
                    method_version=selected_profiler.method_version,
                    schema_version=SCHEMA_VERSION,
                    payload=payload,
                    quality_limitations=normalized_limitations,
                )
                session.commit()
        except Exception:
            session.rollback()
            raise

        return EventFragmentProfileReport(
            document_version_id=document_version_id,
            event_observation_artifact_id=observation_artifact.id,
            event_fragment_profile_artifact_id=(
                None if profile_artifact is None else profile_artifact.id
            ),
            profile_method=selected_profiler.method,
            profile_method_version=selected_profiler.method_version,
            persisted=persist,
            profiles=tuple(profiles),
            quality_limitations=normalized_limitations,
        )


def _select_observation_artifact(
        session: Session,
        *,
        document_version_id: int,
        artifact_id: int | None,
) -> DerivedArtifact:
    if artifact_id is not None:
        artifact = session.get(DerivedArtifact, artifact_id)
        if artifact is None:
            raise ValueError(
                f"Derived artifact does not exist: {artifact_id}."
            )
        if artifact.document_version_id != document_version_id:
            raise ValueError(
                "Event-observation artifact belongs to another document "
                "version."
            )
        if artifact.artifact_type is not DerivedArtifactType.EVENT_OBSERVATIONS:
            raise ValueError("Selected artifact is not event observations.")
        return artifact

    candidates = DerivedArtifactRepository(session).get_for_version(
        document_version_id,
        artifact_type=DerivedArtifactType.EVENT_OBSERVATIONS,
    )
    if not candidates:
        raise ValueError("Document version has no event-observation artifact.")
    if len(candidates) != 1:
        identifiers = ",".join(str(item.id) for item in candidates)
        raise ValueError(
            "Document version has multiple event-observation artifacts; "
            f"choose --event-observation-artifact-id from: {identifiers}."
        )
    return candidates[0]


def _validate_projection(
        artifact: DerivedArtifact,
        rows: Sequence,
) -> None:
    payload_observations = artifact.payload.get("observations")
    if not isinstance(payload_observations, list):
        raise ValueError("Event-observation artifact payload is inconsistent.")
    if len(payload_observations) != len(rows):
        raise ValueError(
            "Event-observation artifact and projected rows disagree."
        )
    try:
        payload_signatures = Counter((
            item["event_fragment_id"],
            item["observation_type"],
            item["source_label"],
            item["surface_text"],
            item["normalized_value"],
            item["start_char"],
            item["end_char"],
            item["rationale"],
        ) for item in payload_observations)
    except (KeyError, TypeError) as error:
        raise ValueError(
            "Event-observation artifact payload is inconsistent."
        ) from error
    row_signatures = Counter((
        item.event_fragment_candidate_id,
        item.observation_type.value,
        item.source_label,
        item.surface_text,
        item.normalized_value,
        item.start_char,
        item.end_char,
        item.rationale,
    ) for item in rows)
    if payload_signatures != row_signatures:
        raise ValueError(
            "Event-observation artifact and projected rows disagree."
        )


def _validate_profile(
        observations: Sequence[ProfileObservation],
        signals: Sequence[EventFragmentProfileSignal],
        exclusions: Sequence[EventFragmentProfileExclusion],
) -> None:
    expected = Counter(item.observation_id for item in observations)
    actual = Counter(
        observation_id
        for signal in signals
        for observation_id in signal.observation_ids
    )
    actual.update(item.observation_id for item in exclusions)
    if expected != actual:
        raise ValueError(
            "Fragment profile must account for every raw observation exactly "
            "once."
        )


def _payload(
        *,
        observation_artifact: DerivedArtifact,
        profiles: Sequence[EventFragmentProfileView],
) -> dict[str, object]:
    return {
        "event_observation_artifact_id": observation_artifact.id,
        "event_observation_content_hash": observation_artifact.content_hash,
        "profiles": [
            {
                "event_fragment_id": profile.event_fragment_id,
                "signals": [
                    {
                        "observation_type": signal.observation_type.value,
                        "normalized_value": signal.normalized_value,
                        "observation_ids": list(signal.observation_ids),
                        "surface_forms": list(signal.surface_forms),
                        "occurrence_count": signal.occurrence_count,
                        "first_start_char": signal.first_start_char,
                        "last_end_char": signal.last_end_char,
                        "rationale": signal.rationale,
                    }
                    for signal in profile.signals
                ],
                "exclusions": [
                    {
                        "observation_id": item.observation_id,
                        "observation_type": item.observation_type.value,
                        "normalized_value": item.normalized_value,
                        "reason": item.reason.value,
                        "rationale": item.rationale,
                    }
                    for item in profile.exclusions
                ],
            }
            for profile in profiles
        ],
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
