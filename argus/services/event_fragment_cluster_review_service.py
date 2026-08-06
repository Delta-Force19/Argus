from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.event_fragment_cluster_reviews import (
    ComponentReviewStatus,
    EventFragmentClusterReviewResult,
    ProposalReviewStatus,
    ReviewedClusterComponent,
    ReviewedClusterProposal,
)
from argus.models import DerivedArtifact, DocumentVersion
from argus.storage.derived_artifact_repository import DerivedArtifactRepository


METHOD = "manual-event-fragment-cluster-review"
METHOD_VERSION = "1"
SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class EventFragmentClusterReviewReport:
    document_version_id: int
    cluster_proposal_artifact_id: int
    cluster_review_artifact_id: int | None
    persisted: bool
    reviewer: str
    reason: str
    proposals: tuple[ReviewedClusterProposal, ...]
    components: tuple[ReviewedClusterComponent, ...]

    def proposal_status_count(self, status: ProposalReviewStatus) -> int:
        return sum(item.status is status for item in self.proposals)

    def component_status_count(self, status: ComponentReviewStatus) -> int:
        return sum(item.status is status for item in self.components)


def review_event_fragment_clusters(
        *,
        document_version_id: int,
        cluster_proposal_artifact_id: int,
        accepted_proposal_ids: Sequence[int] = (),
        rejected_proposal_ids: Sequence[int] = (),
        preserved_component_fragment_ids: Sequence[Sequence[int]] = (),
        reviewer: str,
        reason: str,
        persist: bool = False,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EventFragmentClusterReviewReport:
    """Validate and optionally persist one complete manual review snapshot."""

    normalized_reviewer = _required(reviewer, "reviewer")
    normalized_reason = _required(reason, "reason")
    with session_factory() as session:
        version = session.get(DocumentVersion, document_version_id)
        if version is None:
            raise ValueError(
                f"Document version does not exist: {document_version_id}."
            )
        source = session.get(DerivedArtifact, cluster_proposal_artifact_id)
        if source is None:
            raise ValueError(
                "Derived artifact does not exist: "
                f"{cluster_proposal_artifact_id}."
            )
        if source.document_version_id != document_version_id:
            raise ValueError("Cluster-proposal artifact belongs to another version.")
        if source.artifact_type is not DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_PROPOSALS:
            raise ValueError("Selected artifact is not event-fragment cluster proposals.")

        result = _review(
            source.payload,
            accepted_proposal_ids=accepted_proposal_ids,
            rejected_proposal_ids=rejected_proposal_ids,
            preserved_component_fragment_ids=preserved_component_fragment_ids,
        )
        artifact = None
        try:
            if persist:
                artifact = DerivedArtifactRepository(session).register(
                    document_version=version,
                    artifact_type=DerivedArtifactType.EVENT_FRAGMENT_CLUSTER_REVIEW,
                    method=METHOD,
                    method_version=METHOD_VERSION,
                    schema_version=SCHEMA_VERSION,
                    payload=_payload(
                        source, result,
                        reviewer=normalized_reviewer,
                        reason=normalized_reason,
                    ),
                    quality_limitations=(
                        "Human review records a decision, not proof of event identity.",
                        "This artifact creates no Event and no fragment assignment.",
                    ),
                )
                session.commit()
        except Exception:
            session.rollback()
            raise
        return EventFragmentClusterReviewReport(
            document_version_id=document_version_id,
            cluster_proposal_artifact_id=source.id,
            cluster_review_artifact_id=None if artifact is None else artifact.id,
            persisted=persist,
            reviewer=normalized_reviewer,
            reason=normalized_reason,
            proposals=result.proposals,
            components=result.components,
        )


def _review(
        payload: Mapping[str, object],
        *,
        accepted_proposal_ids: Sequence[int],
        rejected_proposal_ids: Sequence[int],
        preserved_component_fragment_ids: Sequence[Sequence[int]],
) -> EventFragmentClusterReviewResult:
    proposals, components = _source_items(payload)
    known_ids = {item[0] for item in proposals}
    accepted = _positive_set(accepted_proposal_ids, "accepted proposal")
    rejected = _positive_set(rejected_proposal_ids, "rejected proposal")
    unknown = (accepted | rejected) - known_ids
    if unknown:
        raise ValueError(
            "Review references unknown proposal ids: "
            + ",".join(map(str, sorted(unknown))) + "."
        )
    overlap = accepted & rejected
    if overlap:
        raise ValueError(
            "A proposal cannot be both accepted and rejected: "
            + ",".join(map(str, sorted(overlap))) + "."
        )

    fragments_by_id = {proposal_id: fragment_ids for proposal_id, fragment_ids in proposals}
    for left_id in accepted:
        for right_id in accepted:
            if left_id < right_id and set(fragments_by_id[left_id]) & set(fragments_by_id[right_id]):
                raise ValueError(
                    "Overlapping cluster proposals cannot both be accepted: "
                    f"{left_id},{right_id}."
                )

    preserved = {
        _fragment_key(values) for values in preserved_component_fragment_ids
    }
    known_components = {item[0] for item in components}
    unknown_components = preserved - known_components
    if unknown_components:
        formatted = ";".join(
            ",".join(map(str, values)) for values in sorted(unknown_components)
        )
        raise ValueError(
            "Review references unknown component fragment ids: "
            f"{formatted}."
        )
    non_ambiguous = preserved - {
        fragment_ids
        for fragment_ids, _, source_status in components
        if source_status == "ambiguous"
    }
    if non_ambiguous:
        raise ValueError(
            "Only an ambiguous source component can preserve ambiguity."
        )

    reviewed_proposals = tuple(
        ReviewedClusterProposal(
            proposal_id,
            fragment_ids,
            ProposalReviewStatus.ACCEPTED if proposal_id in accepted else (
                ProposalReviewStatus.REJECTED if proposal_id in rejected
                else ProposalReviewStatus.PENDING
            ),
        )
        for proposal_id, fragment_ids in proposals
    )
    status_by_id = {item.proposal_id: item.status for item in reviewed_proposals}
    reviewed_components = []
    for fragment_ids, proposal_ids, source_status in components:
        statuses = [status_by_id[value] for value in proposal_ids]
        accepted_ids = [
            value for value in proposal_ids
            if status_by_id[value] is ProposalReviewStatus.ACCEPTED
        ]
        if source_status == "isolated":
            status = ComponentReviewStatus.ISOLATED
            rationale = "Source component has no cluster proposal to review."
        elif fragment_ids in preserved:
            if any(value is not ProposalReviewStatus.PENDING for value in statuses):
                raise ValueError(
                    "A preserved-ambiguity component cannot also contain accepted or rejected proposals."
                )
            status = ComponentReviewStatus.PRESERVED_AMBIGUITY
            rationale = "Reviewer explicitly preserved the competing alternatives."
        elif len(accepted_ids) == 1 and all(
                value is not ProposalReviewStatus.PENDING for value in statuses
        ):
            status = ComponentReviewStatus.RESOLVED
            rationale = "One proposal was accepted and every alternative was rejected."
        elif statuses and all(value is ProposalReviewStatus.REJECTED for value in statuses):
            status = ComponentReviewStatus.REJECTED
            rationale = "Reviewer rejected every proposal in the component."
        else:
            status = ComponentReviewStatus.PENDING
            rationale = "The component does not yet have a complete explicit decision."
        reviewed_components.append(ReviewedClusterComponent(
            fragment_ids,
            proposal_ids,
            status,
            accepted_ids[0] if len(accepted_ids) == 1 else None,
            rationale,
        ))
    return EventFragmentClusterReviewResult(
        reviewed_proposals, tuple(reviewed_components)
    )


def _source_items(payload: Mapping[str, object]):
    raw_proposals = payload.get("proposals")
    raw_components = payload.get("components")
    if not isinstance(raw_proposals, list) or not isinstance(raw_components, list):
        raise ValueError("Cluster-proposal artifact payload is inconsistent.")
    try:
        proposals = []
        for item in raw_proposals:
            proposal_id = item["proposal_id"]
            fragment_ids = _fragment_key(item["event_fragment_ids"])
            if not isinstance(proposal_id, int) or proposal_id < 1 or len(fragment_ids) < 2:
                raise ValueError
            proposals.append((proposal_id, fragment_ids))
        if len({item[0] for item in proposals}) != len(proposals):
            raise ValueError
        known_ids = {item[0] for item in proposals}
        components = []
        for item in raw_components:
            fragment_ids = _fragment_key(item["event_fragment_ids"])
            proposal_ids = tuple(item["proposal_ids"])
            source_status = item["status"]
            if (
                    not fragment_ids
                    or not isinstance(item["proposal_ids"], list)
                    or any(not isinstance(value, int) for value in proposal_ids)
                    or not set(proposal_ids) <= known_ids
                    or source_status not in {"coherent", "ambiguous", "isolated"}
                    or (source_status == "isolated" and proposal_ids)
                    or any(
                        not set(dict(proposals)[proposal_id]) <= set(fragment_ids)
                        for proposal_id in proposal_ids
                    )
            ):
                raise ValueError
            components.append((fragment_ids, proposal_ids, source_status))
        component_keys = {item[0] for item in components}
        if len(component_keys) != len(components):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Cluster-proposal artifact payload is inconsistent.") from error
    return tuple(proposals), tuple(components)


def _fragment_key(values: Sequence[int]) -> tuple[int, ...]:
    if (
            isinstance(values, (str, bytes))
            or not values
            or any(not isinstance(value, int) or value < 1 for value in values)
            or len(set(values)) != len(values)
    ):
        raise ValueError("Component fragment ids must be unique positive integers.")
    return tuple(sorted(values))


def _positive_set(values: Sequence[int], label: str) -> set[int]:
    if any(not isinstance(value, int) or value < 1 for value in values):
        raise ValueError(f"{label} ids must be positive integers.")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} ids must not contain duplicates.")
    return set(values)


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank.")
    return normalized


def _payload(source, result, *, reviewer, reason):
    return {
        "cluster_proposal_artifact_id": source.id,
        "cluster_proposal_content_hash": source.content_hash,
        "reviewer": reviewer,
        "reason": reason,
        "proposals": [{
            "proposal_id": item.proposal_id,
            "event_fragment_ids": list(item.event_fragment_ids),
            "status": item.status.value,
        } for item in result.proposals],
        "components": [{
            "event_fragment_ids": list(item.event_fragment_ids),
            "proposal_ids": list(item.proposal_ids),
            "status": item.status.value,
            "accepted_proposal_id": item.accepted_proposal_id,
            "rationale": item.rationale,
        } for item in result.components],
    }
