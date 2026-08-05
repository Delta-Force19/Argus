from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.analysis.deterministic_event_fragment_pair_comparator import (
    DeterministicEventFragmentPairComparator,
)
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairStatus,
    FragmentProfile,
    FragmentProfileSignal,
)
from argus.event_observations import EventObservationType
from argus.models import DerivedArtifact, DocumentVersion
from argus.storage.derived_artifact_repository import DerivedArtifactRepository


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class FragmentPairCandidateReport:
    document_version_id: int
    event_fragment_profile_artifact_id: int
    fragment_pair_candidate_artifact_id: int | None
    method: str
    method_version: str
    persisted: bool
    pairs: tuple[FragmentPairCandidate, ...]
    quality_limitations: tuple[str, ...]

    def status_count(self, status: FragmentPairStatus) -> int:
        return sum(item.status is status for item in self.pairs)


def compare_event_fragment_profiles(
        *,
        document_version_id: int,
        event_fragment_profile_artifact_id: int | None = None,
        persist: bool = False,
        comparator: DeterministicEventFragmentPairComparator | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> FragmentPairCandidateReport:
    """Audit every pair from one exact immutable fragment-profile artifact."""

    selected = comparator or DeterministicEventFragmentPairComparator()
    with session_factory() as session:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        source = _select_profile_artifact(
            session,
            document_version_id=document_version_id,
            artifact_id=event_fragment_profile_artifact_id,
        )
        profiles = _profiles_from_payload(source.payload)
        result = selected.compare(profiles)
        limitations = _unique((
            *result.quality_limitations,
            *source.quality_limitations,
        ))
        artifact = None
        try:
            if persist:
                artifact = DerivedArtifactRepository(session).register(
                    document_version=version,
                    artifact_type=(
                        DerivedArtifactType.EVENT_FRAGMENT_PAIR_CANDIDATES
                    ),
                    method=selected.method,
                    method_version=selected.method_version,
                    schema_version=SCHEMA_VERSION,
                    payload=_payload(source, result.pairs),
                    quality_limitations=limitations,
                )
                session.commit()
        except Exception:
            session.rollback()
            raise
        return FragmentPairCandidateReport(
            document_version_id=document_version_id,
            event_fragment_profile_artifact_id=source.id,
            fragment_pair_candidate_artifact_id=(
                None if artifact is None else artifact.id
            ),
            method=selected.method,
            method_version=selected.method_version,
            persisted=persist,
            pairs=result.pairs,
            quality_limitations=limitations,
        )


def _select_profile_artifact(
        session: Session,
        *,
        document_version_id: int,
        artifact_id: int | None,
) -> DerivedArtifact:
    if artifact_id is not None:
        artifact = session.get(DerivedArtifact, artifact_id)
        if artifact is None:
            raise ValueError(f"Derived artifact does not exist: {artifact_id}.")
        if artifact.document_version_id != document_version_id:
            raise ValueError("Fragment-profile artifact belongs to another version.")
        if artifact.artifact_type is not DerivedArtifactType.EVENT_FRAGMENT_PROFILES:
            raise ValueError("Selected artifact is not event fragment profiles.")
        return artifact
    candidates = DerivedArtifactRepository(session).get_for_version(
        document_version_id,
        artifact_type=DerivedArtifactType.EVENT_FRAGMENT_PROFILES,
    )
    if not candidates:
        raise ValueError("Document version has no fragment-profile artifact.")
    if len(candidates) != 1:
        identifiers = ",".join(str(item.id) for item in candidates)
        raise ValueError(
            "Document version has multiple fragment-profile artifacts; "
            f"choose --event-fragment-profile-artifact-id from: {identifiers}."
        )
    return candidates[0]


def _profiles_from_payload(payload: Mapping[str, object]) -> tuple[FragmentProfile, ...]:
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("Fragment-profile artifact payload is inconsistent.")
    profiles: list[FragmentProfile] = []
    try:
        for raw_profile in raw_profiles:
            fragment_id = raw_profile["event_fragment_id"]
            raw_signals = raw_profile["signals"]
            if not isinstance(fragment_id, int) or fragment_id < 1:
                raise ValueError
            if not isinstance(raw_signals, list):
                raise ValueError
            signals_list: list[FragmentProfileSignal] = []
            for item in raw_signals:
                value = item["normalized_value"]
                observation_ids = item["observation_ids"]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError
                if (
                        not isinstance(observation_ids, list)
                        or not observation_ids
                        or any(
                            not isinstance(identifier, int) or identifier < 1
                            for identifier in observation_ids
                        )
                ):
                    raise ValueError
                signals_list.append(FragmentProfileSignal(
                    observation_type=EventObservationType(
                        item["observation_type"]
                    ),
                    normalized_value=value,
                    observation_ids=tuple(observation_ids),
                ))
            profiles.append(FragmentProfile(
                event_fragment_id=fragment_id,
                signals=tuple(signals_list),
            ))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Fragment-profile artifact payload is inconsistent."
        ) from error
    if len(profiles) < 2:
        raise ValueError("At least two fragment profiles are required.")
    return tuple(profiles)


def _payload(
        source: DerivedArtifact,
        pairs: Sequence[FragmentPairCandidate],
) -> dict[str, object]:
    return {
        "event_fragment_profile_artifact_id": source.id,
        "event_fragment_profile_content_hash": source.content_hash,
        "pairs": [{
            "left_event_fragment_id": pair.left_event_fragment_id,
            "right_event_fragment_id": pair.right_event_fragment_id,
            "status": pair.status.value,
            "evidence_dimensions": [item.value for item in pair.evidence_dimensions],
            "evidence_points": pair.evidence_points,
            "rationale": pair.rationale,
            "matches": [{
                "observation_type": match.observation_type.value,
                "normalized_value": match.normalized_value,
                "left_observation_ids": list(match.left_observation_ids),
                "right_observation_ids": list(match.right_observation_ids),
                "evidence_points": match.evidence_points,
                "rationale": match.rationale,
            } for match in pair.matches],
        } for pair in pairs],
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
