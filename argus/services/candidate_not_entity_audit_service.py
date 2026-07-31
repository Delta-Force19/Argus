from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.knowledge import (
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
)
from argus.models import (
    CandidateResolutionDecision,
    CandidateResolutionExclusion,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityMention,
)
from argus.services.entity_candidate_provenance_service import (
    resolve_entity_candidate_provenance,
)


class CandidateNotEntityValidity(str, Enum):
    """Current validity of one reviewed not-entity scope."""

    ACTIVE = "active"
    REVOKED = "revoked"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CandidateNotEntityAuditItem:
    """One current or revoked not-entity judgment and exact evidence."""

    seed_candidate_id: int
    entity_type: EntityType
    canonical_text: str
    scope: CandidateResolutionScope
    latest_decision_id: int
    latest_revision: int
    latest_status: CandidateResolutionStatus
    applied_decision_id: int
    matched_candidate_ids: tuple[int, ...]
    reason: str
    reviewer: str
    validity: CandidateNotEntityValidity
    issue: str | None


@dataclass(frozen=True, slots=True)
class CandidateNotEntitySnapshot:
    """Complete fail-closed view of explicit non-entity decisions."""

    active_candidate_ids: tuple[int, ...]
    invalid_candidate_ids: tuple[int, ...]
    items: tuple[CandidateNotEntityAuditItem, ...]


def evaluate_candidate_not_entity_validity(
        session: Session,
) -> CandidateNotEntitySnapshot:
    """Audit latest not-entity decisions and their frozen candidate set."""

    decisions = tuple(
        session.scalars(
            select(CandidateResolutionDecision).order_by(
                CandidateResolutionDecision
                .seed_entity_candidate_id.asc(),
                CandidateResolutionDecision.revision.asc(),
                CandidateResolutionDecision.id.asc(),
            )
        ).all()
    )
    histories: dict[int, list[CandidateResolutionDecision]] = defaultdict(list)
    for decision in decisions:
        histories[decision.seed_entity_candidate_id].append(decision)

    exclusions = tuple(
        session.scalars(
            select(CandidateResolutionExclusion).order_by(
                CandidateResolutionExclusion
                .candidate_resolution_decision_id.asc(),
                CandidateResolutionExclusion.entity_candidate_id.asc(),
                CandidateResolutionExclusion.id.asc(),
            )
        ).all()
    )
    exclusions_by_decision: dict[int, list[int]] = defaultdict(list)
    for exclusion in exclusions:
        exclusions_by_decision[
            exclusion.candidate_resolution_decision_id
        ].append(exclusion.entity_candidate_id)

    relevant: list[
        tuple[
            CandidateResolutionDecision,
            CandidateResolutionDecision,
        ]
    ] = []
    for history in histories.values():
        latest = history[-1]
        if latest.status is CandidateResolutionStatus.NOT_ENTITY:
            relevant.append((latest, latest))
        elif (
                latest.status is CandidateResolutionStatus.REVOKED
                and len(history) > 1
                and history[-2].status
                is CandidateResolutionStatus.NOT_ENTITY
        ):
            relevant.append((latest, history[-2]))

    candidate_ids = {
        decision.seed_entity_candidate_id
        for latest, decision in relevant
    }
    for _, decision in relevant:
        candidate_ids.update(exclusions_by_decision.get(decision.id, ()))
    candidates = {
        candidate.id: candidate
        for candidate in session.scalars(
            select(EntityCandidate).where(EntityCandidate.id.in_(candidate_ids))
        )
    } if candidate_ids else {}
    mention_ids = {
        candidate.entity_mention_id for candidate in candidates.values()
    }
    mentions = {
        mention.id: mention
        for mention in session.scalars(
            select(EntityMention).where(EntityMention.id.in_(mention_ids))
        )
    } if mention_ids else {}
    assigned_candidate_ids = set(
        session.scalars(
            select(EntityCandidateAssignment.entity_candidate_id)
        ).all()
    )

    items: list[CandidateNotEntityAuditItem] = []
    active_candidate_ids: set[int] = set()
    invalid_candidate_ids: set[int] = set()
    claimed_candidate_ids: set[int] = set()
    for latest, applied in sorted(
            relevant,
            key=lambda pair: pair[0].seed_entity_candidate_id,
    ):
        seed = candidates.get(latest.seed_entity_candidate_id)
        matched_ids = tuple(
            sorted(exclusions_by_decision.get(applied.id, ()))
        )
        issue = _validate_applied_scope(
            session,
            latest=latest,
            applied=applied,
            seed=seed,
            matched_ids=matched_ids,
            candidates=candidates,
            mentions=mentions,
            assigned_candidate_ids=assigned_candidate_ids,
            claimed_candidate_ids=claimed_candidate_ids,
        )
        if latest.status is CandidateResolutionStatus.REVOKED:
            validity = CandidateNotEntityValidity.REVOKED
        elif issue is not None:
            validity = CandidateNotEntityValidity.INVALID
            invalid_candidate_ids.update(
                matched_ids or (latest.seed_entity_candidate_id,)
            )
            invalid_candidate_ids.add(latest.seed_entity_candidate_id)
        else:
            validity = CandidateNotEntityValidity.ACTIVE
            active_candidate_ids.update(matched_ids)
            claimed_candidate_ids.update(matched_ids)
        items.append(
            CandidateNotEntityAuditItem(
                seed_candidate_id=latest.seed_entity_candidate_id,
                entity_type=(
                    seed.entity_type if seed is not None else EntityType.OTHER
                ),
                canonical_text=(
                    seed.canonical_text if seed is not None else "<missing>"
                ),
                scope=latest.scope,
                latest_decision_id=latest.id,
                latest_revision=latest.revision,
                latest_status=latest.status,
                applied_decision_id=applied.id,
                matched_candidate_ids=matched_ids,
                reason=applied.reason,
                reviewer=applied.reviewer,
                validity=validity,
                issue=issue,
            )
        )

    return CandidateNotEntitySnapshot(
        active_candidate_ids=tuple(sorted(active_candidate_ids)),
        invalid_candidate_ids=tuple(sorted(invalid_candidate_ids)),
        items=tuple(items),
    )


def _validate_applied_scope(
        session: Session,
        *,
        latest: CandidateResolutionDecision,
        applied: CandidateResolutionDecision,
        seed: EntityCandidate | None,
        matched_ids: tuple[int, ...],
        candidates: dict[int, EntityCandidate],
        mentions: dict[int, EntityMention],
        assigned_candidate_ids: set[int],
        claimed_candidate_ids: set[int],
) -> str | None:
    if seed is None:
        return "Not-entity decision references a missing seed candidate."
    if applied.status is not CandidateResolutionStatus.NOT_ENTITY:
        return "Not-entity evidence references a non-exclusion decision."
    if applied.entity_id is not None or latest.entity_id is not None:
        return "Not-entity decision unexpectedly references an entity."
    if not matched_ids or seed.id not in matched_ids:
        return "Not-entity evidence does not include its seed candidate."
    if len(matched_ids) != len(set(matched_ids)):
        return "Not-entity evidence contains duplicate candidates."
    if any(candidate_id in claimed_candidate_ids for candidate_id in matched_ids):
        return "Candidate is covered by multiple active not-entity decisions."
    for candidate_id in matched_ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            return "Not-entity evidence references a missing candidate."
        if applied.scope is CandidateResolutionScope.SINGLE:
            if candidate.id != seed.id or len(matched_ids) != 1:
                return "Single not-entity decision covers another candidate."
        elif (
                candidate.entity_type is not seed.entity_type
                or candidate.canonical_text != seed.canonical_text
        ):
            return "Exact-canonical not-entity evidence does not match its seed."
        if candidate_id in assigned_candidate_ids:
            return "Not-entity candidate is also assigned to an entity."
        _, issue = resolve_entity_candidate_provenance(
            session,
            candidate=candidate,
            mention=mentions.get(candidate.entity_mention_id),
            document_version_id=candidate.document_version_id,
        )
        if issue is not None:
            return f"Not-entity candidate provenance is invalid: {issue}"
    return None
