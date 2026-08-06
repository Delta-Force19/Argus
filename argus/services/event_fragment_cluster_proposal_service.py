from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.analysis.deterministic_event_fragment_cluster_proposer import (
    DeterministicEventFragmentClusterProposer,
)
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.event_fragment_cluster_proposals import (
    CandidateGraphComponent,
    ClusterComponentStatus,
    EventFragmentClusterProposal,
)
from argus.event_fragment_pair_candidates import (
    FragmentPairCandidate,
    FragmentPairMatch,
    FragmentPairStatus,
)
from argus.event_observations import EventObservationType
from argus.models import DerivedArtifact, DocumentVersion
from argus.storage.derived_artifact_repository import DerivedArtifactRepository


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class EventFragmentClusterProposalReport:
    document_version_id: int
    fragment_pair_candidate_artifact_id: int
    cluster_proposal_artifact_id: int | None
    method: str
    method_version: str
    persisted: bool
    proposals: tuple[EventFragmentClusterProposal, ...]
    components: tuple[CandidateGraphComponent, ...]
    quality_limitations: tuple[str, ...]

    def component_status_count(self, status: ClusterComponentStatus) -> int:
        return sum(item.status is status for item in self.components)


def propose_event_fragment_clusters(
        *,
        document_version_id: int,
        fragment_pair_candidate_artifact_id: int | None = None,
        persist: bool = False,
        proposer: DeterministicEventFragmentClusterProposer | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EventFragmentClusterProposalReport:
    """Create review-only maximal-clique proposals from one exact pair audit."""

    selected = proposer or DeterministicEventFragmentClusterProposer()
    with session_factory() as session:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        source = _select_pair_artifact(
            session,
            document_version_id=document_version_id,
            artifact_id=fragment_pair_candidate_artifact_id,
        )
        result = selected.propose(_pairs_from_payload(source.payload))
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
                        DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_PROPOSALS
                    ),
                    method=selected.method,
                    method_version=selected.method_version,
                    schema_version=SCHEMA_VERSION,
                    payload=_payload(source, result.proposals, result.components),
                    quality_limitations=limitations,
                )
                session.commit()
        except Exception:
            session.rollback()
            raise
        return EventFragmentClusterProposalReport(
            document_version_id=document_version_id,
            fragment_pair_candidate_artifact_id=source.id,
            cluster_proposal_artifact_id=None if artifact is None else artifact.id,
            method=selected.method,
            method_version=selected.method_version,
            persisted=persist,
            proposals=result.proposals,
            components=result.components,
            quality_limitations=limitations,
        )


def _select_pair_artifact(
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
            raise ValueError("Fragment-pair artifact belongs to another version.")
        if (
                artifact.artifact_type
                is not DerivedArtifactType.EVENT_FRAGMENT_PAIR_CANDIDATES
        ):
            raise ValueError("Selected artifact is not fragment-pair candidates.")
        return artifact
    candidates = DerivedArtifactRepository(session).get_for_version(
        document_version_id,
        artifact_type=DerivedArtifactType.EVENT_FRAGMENT_PAIR_CANDIDATES,
    )
    if not candidates:
        raise ValueError("Document version has no fragment-pair candidate artifact.")
    if len(candidates) != 1:
        identifiers = ",".join(str(item.id) for item in candidates)
        raise ValueError(
            "Document version has multiple fragment-pair candidate artifacts; "
            "choose --fragment-pair-candidate-artifact-id from: "
            f"{identifiers}."
        )
    return candidates[0]


def _pairs_from_payload(
        payload: Mapping[str, object],
) -> tuple[FragmentPairCandidate, ...]:
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list):
        raise ValueError("Fragment-pair artifact payload is inconsistent.")
    pairs: list[FragmentPairCandidate] = []
    try:
        for item in raw_pairs:
            raw_dimensions = item["evidence_dimensions"]
            raw_matches = item["matches"]
            if (
                    not isinstance(raw_dimensions, list)
                    or not isinstance(raw_matches, list)
            ):
                raise ValueError
            matches = tuple(_match_from_payload(match) for match in raw_matches)
            pair = FragmentPairCandidate(
                left_event_fragment_id=item["left_event_fragment_id"],
                right_event_fragment_id=item["right_event_fragment_id"],
                status=FragmentPairStatus(item["status"]),
                evidence_dimensions=tuple(
                    EventObservationType(value) for value in raw_dimensions
                ),
                evidence_points=item["evidence_points"],
                matches=matches,
                rationale=item["rationale"],
            )
            match_dimensions = tuple(sorted(
                {match.observation_type for match in matches},
                key=lambda value: value.value,
            ))
            if (
                    not isinstance(pair.left_event_fragment_id, int)
                    or not isinstance(pair.right_event_fragment_id, int)
                    or not isinstance(pair.evidence_points, int)
                    or pair.evidence_points < 0
                    or not isinstance(pair.rationale, str)
                    or not pair.rationale.strip()
                    or pair.evidence_dimensions != match_dimensions
                    or pair.evidence_points != sum(
                        match.evidence_points for match in matches
                    )
            ):
                raise ValueError
            pairs.append(pair)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Fragment-pair artifact payload is inconsistent."
        ) from error
    return tuple(pairs)


def _match_from_payload(item: Mapping[str, object]) -> FragmentPairMatch:
    left_ids = item["left_observation_ids"]
    right_ids = item["right_observation_ids"]
    if (
            not isinstance(left_ids, list)
            or not isinstance(right_ids, list)
            or not left_ids
            or not right_ids
            or any(not isinstance(value, int) or value < 1 for value in left_ids)
            or any(not isinstance(value, int) or value < 1 for value in right_ids)
            or not isinstance(item["normalized_value"], str)
            or not item["normalized_value"].strip()
            or not isinstance(item["evidence_points"], int)
            or item["evidence_points"] < 0
            or not isinstance(item["rationale"], str)
            or not item["rationale"].strip()
    ):
        raise ValueError
    return FragmentPairMatch(
        observation_type=EventObservationType(item["observation_type"]),
        normalized_value=item["normalized_value"],
        left_observation_ids=tuple(left_ids),
        right_observation_ids=tuple(right_ids),
        evidence_points=item["evidence_points"],
        rationale=item["rationale"],
    )


def _payload(
        source: DerivedArtifact,
        proposals: Sequence[EventFragmentClusterProposal],
        components: Sequence[CandidateGraphComponent],
) -> dict[str, object]:
    return {
        "fragment_pair_candidate_artifact_id": source.id,
        "fragment_pair_candidate_content_hash": source.content_hash,
        "proposals": [{
            "proposal_id": item.proposal_id,
            "event_fragment_ids": list(item.event_fragment_ids),
            "evidence_dimensions": [value.value for value in item.evidence_dimensions],
            "evidence_points": item.evidence_points,
            "rationale": item.rationale,
            "supporting_pairs": [{
                "left_event_fragment_id": pair.left_event_fragment_id,
                "right_event_fragment_id": pair.right_event_fragment_id,
                "evidence_dimensions": [
                    value.value for value in pair.evidence_dimensions
                ],
                "evidence_points": pair.evidence_points,
            } for pair in item.supporting_pairs],
        } for item in proposals],
        "components": [{
            "event_fragment_ids": list(item.event_fragment_ids),
            "status": item.status.value,
            "proposal_ids": list(item.proposal_ids),
            "candidate_pair_count": item.candidate_pair_count,
            "rationale": item.rationale,
            "blocking_pairs": [{
                "left_event_fragment_id": pair.left_event_fragment_id,
                "right_event_fragment_id": pair.right_event_fragment_id,
                "status": pair.status.value,
                "rationale": pair.rationale,
            } for pair in item.blocking_pairs],
        } for item in components],
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
