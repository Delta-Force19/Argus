from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.knowledge import (
    AliasDecisionStatus,
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
)
from argus.models import (
    AliasDecision,
    AliasProposal,
    CandidateResolutionDecision,
    CandidateResolutionEvidence,
    Entity,
    EntityCandidate,
    EntityCandidateAssignment,
    EntityResolutionEvidence,
)
from argus.services.candidate_not_entity_audit_service import (
    CandidateNotEntityAuditItem,
    evaluate_candidate_not_entity_validity,
)


class EntityResolutionValidity(str, Enum):
    """Current validity of one applied identity-resolution link."""

    ACTIVE = "active"
    PENDING_REAPPLICATION = "pending_reapplication"
    NEEDS_REVIEW = "needs_review"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ResolutionValidityCount:
    """Count of registry links in one validity state."""

    validity: EntityResolutionValidity
    count: int


@dataclass(frozen=True, slots=True)
class EntityRegistryAuditItem:
    """One entity/proposal link with current review provenance."""

    entity_id: int
    entity_type: EntityType
    canonical_name: str
    safe_for_downstream_use: bool
    proposal_id: int
    left_candidate_id: int
    right_candidate_id: int
    applied_decision_ids: tuple[int, ...]
    latest_decision_id: int
    latest_revision: int
    latest_status: AliasDecisionStatus
    validity: EntityResolutionValidity


@dataclass(frozen=True, slots=True)
class EntityRegistryAuditReport:
    """Conservative read-only validity view over the entity registry."""

    entity_count: int
    safe_entity_count: int
    blocked_entity_count: int
    link_count: int
    counts_by_validity: tuple[ResolutionValidityCount, ...]
    items: tuple[EntityRegistryAuditItem, ...]
    candidate_items: tuple["CandidateResolutionAuditItem", ...] = ()
    not_entity_items: tuple[CandidateNotEntityAuditItem, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateResolutionAuditItem:
    """One entity/candidate decision link with current validity."""

    entity_id: int
    entity_type: EntityType
    canonical_name: str
    safe_for_downstream_use: bool
    seed_candidate_id: int
    seed_canonical_text: str
    scope: CandidateResolutionScope
    applied_decision_ids: tuple[int, ...]
    latest_decision_id: int
    latest_revision: int
    latest_status: CandidateResolutionStatus
    validity: EntityResolutionValidity


@dataclass(frozen=True, slots=True)
class EntityRegistryValiditySnapshot:
    """Complete detached validity boundary for registry consumers."""

    entity_count: int
    safe_entity_ids: tuple[int, ...]
    blocked_entity_ids: tuple[int, ...]
    counts_by_validity: tuple[ResolutionValidityCount, ...]
    items: tuple[EntityRegistryAuditItem, ...]
    candidate_items: tuple[CandidateResolutionAuditItem, ...]
    not_entity_items: tuple[CandidateNotEntityAuditItem, ...] = ()
    not_entity_candidate_ids: tuple[int, ...] = ()
    invalid_not_entity_candidate_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _RegistryRows:
    entities: tuple[Entity, ...]
    assignments: tuple[EntityCandidateAssignment, ...]
    evidence: tuple[EntityResolutionEvidence, ...]
    proposals: dict[int, AliasProposal]
    decisions: dict[int, AliasDecision]
    histories: dict[int, tuple[AliasDecision, ...]]
    candidates: dict[int, EntityCandidate]
    candidate_decisions: dict[int, CandidateResolutionDecision]
    candidate_histories: dict[
        int,
        tuple[CandidateResolutionDecision, ...],
    ]
    candidate_evidence: tuple[CandidateResolutionEvidence, ...]


def get_entity_registry_audit(
        *,
        limit: int = 50,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EntityRegistryAuditReport:
    """Audit current registry validity without changing stored history."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    with session_factory() as session:
        snapshot = evaluate_entity_registry_validity(session)

    return EntityRegistryAuditReport(
        entity_count=snapshot.entity_count,
        safe_entity_count=len(snapshot.safe_entity_ids),
        blocked_entity_count=len(snapshot.blocked_entity_ids),
        link_count=(
            len(snapshot.items) + len(snapshot.candidate_items)
        ),
        counts_by_validity=snapshot.counts_by_validity,
        items=snapshot.items[:limit],
        candidate_items=snapshot.candidate_items[:limit],
        not_entity_items=snapshot.not_entity_items[:limit],
    )


def evaluate_entity_registry_validity(
        session: Session,
) -> EntityRegistryValiditySnapshot:
    """Compute the complete conservative registry validity boundary."""

    rows = _load_rows(session)
    not_entity = evaluate_candidate_not_entity_validity(session)
    items = _build_items(rows)
    candidate_items = _build_candidate_items(rows)
    grouped_items = _group_by_entity(items)
    grouped_candidate_items = _group_candidate_items_by_entity(
        candidate_items
    )
    entity_safety = {
        entity.id: (
            bool(
                grouped_items.get(entity.id)
                or grouped_candidate_items.get(entity.id)
            )
            and all(
                item.validity is EntityResolutionValidity.ACTIVE
                for item in grouped_items.get(entity.id, ())
            )
            and all(
                item.validity is EntityResolutionValidity.ACTIVE
                for item in grouped_candidate_items.get(entity.id, ())
            )
        )
        for entity in rows.entities
    }
    detached_items = tuple(
        _with_entity_safety(
            item,
            safe_for_downstream_use=entity_safety[item.entity_id],
        )
        for item in items
    )
    detached_candidate_items = tuple(
        _with_candidate_entity_safety(
            item,
            safe_for_downstream_use=entity_safety[item.entity_id],
        )
        for item in candidate_items
    )
    counts = Counter(
        item.validity
        for item in (*items, *candidate_items)
    )

    return EntityRegistryValiditySnapshot(
        entity_count=len(rows.entities),
        safe_entity_ids=tuple(
            entity.id
            for entity in rows.entities
            if entity_safety[entity.id]
        ),
        blocked_entity_ids=tuple(
            entity.id
            for entity in rows.entities
            if not entity_safety[entity.id]
        ),
        counts_by_validity=tuple(
            ResolutionValidityCount(
                validity=validity,
                count=counts.get(validity, 0),
            )
            for validity in EntityResolutionValidity
            if counts.get(validity, 0)
        ),
        items=detached_items,
        candidate_items=detached_candidate_items,
        not_entity_items=not_entity.items,
        not_entity_candidate_ids=not_entity.active_candidate_ids,
        invalid_not_entity_candidate_ids=(
            not_entity.invalid_candidate_ids
        ),
    )


def _load_rows(session: Session) -> _RegistryRows:
    entities = tuple(
        session.scalars(select(Entity).order_by(Entity.id.asc())).all()
    )
    assignments = tuple(
        session.scalars(
            select(EntityCandidateAssignment).order_by(
                EntityCandidateAssignment.id.asc()
            )
        ).all()
    )
    evidence = tuple(
        session.scalars(
            select(EntityResolutionEvidence).order_by(
                EntityResolutionEvidence.id.asc()
            )
        ).all()
    )
    decisions = tuple(
        session.scalars(
            select(AliasDecision).order_by(
                AliasDecision.alias_proposal_id.asc(),
                AliasDecision.revision.asc(),
                AliasDecision.id.asc(),
            )
        ).all()
    )
    decision_by_id = {decision.id: decision for decision in decisions}
    proposal_ids = {
        decision.alias_proposal_id for decision in decisions
    }
    proposals = (
        {
            proposal.id: proposal
            for proposal in session.scalars(
                select(AliasProposal).where(
                    AliasProposal.id.in_(proposal_ids)
                )
            )
        }
        if proposal_ids
        else {}
    )
    histories: dict[int, list[AliasDecision]] = defaultdict(list)
    for decision in decisions:
        histories[decision.alias_proposal_id].append(decision)
    candidate_decisions = tuple(
        session.scalars(
            select(CandidateResolutionDecision).order_by(
                CandidateResolutionDecision.seed_entity_candidate_id.asc(),
                CandidateResolutionDecision.revision.asc(),
                CandidateResolutionDecision.id.asc(),
            )
        ).all()
    )
    candidate_histories: dict[
        int,
        list[CandidateResolutionDecision],
    ] = defaultdict(list)
    for decision in candidate_decisions:
        candidate_histories[
            decision.seed_entity_candidate_id
        ].append(decision)
    candidate_ids = {
        decision.seed_entity_candidate_id
        for decision in candidate_decisions
    }
    candidate_ids.update(
        assignment.entity_candidate_id
        for assignment in assignments
        if (
            assignment
            .assigned_by_candidate_resolution_decision_id
            is not None
        )
    )
    candidates = (
        {
            candidate.id: candidate
            for candidate in session.scalars(
                select(EntityCandidate).where(
                    EntityCandidate.id.in_(candidate_ids)
                )
            )
        }
        if candidate_ids
        else {}
    )
    candidate_evidence = tuple(
        session.scalars(
            select(CandidateResolutionEvidence).order_by(
                CandidateResolutionEvidence.id.asc()
            )
        ).all()
    )

    return _RegistryRows(
        entities=entities,
        assignments=assignments,
        evidence=evidence,
        proposals=proposals,
        decisions=decision_by_id,
        histories={
            proposal_id: tuple(history)
            for proposal_id, history in histories.items()
        },
        candidates=candidates,
        candidate_decisions={
            decision.id: decision
            for decision in candidate_decisions
        },
        candidate_histories={
            candidate_id: tuple(history)
            for candidate_id, history in candidate_histories.items()
        },
        candidate_evidence=candidate_evidence,
    )


def _build_items(
        rows: _RegistryRows,
) -> tuple[EntityRegistryAuditItem, ...]:
    evidence_by_link: dict[
        tuple[int, int],
        list[AliasDecision],
    ] = defaultdict(list)
    proposal_ids_by_entity: dict[int, set[int]] = defaultdict(set)

    for evidence in rows.evidence:
        decision = _required_decision(
            rows,
            evidence.alias_decision_id,
        )
        link = (evidence.entity_id, decision.alias_proposal_id)
        evidence_by_link[link].append(decision)
        proposal_ids_by_entity[evidence.entity_id].add(
            decision.alias_proposal_id
        )

    for assignment in rows.assignments:
        if assignment.assigned_by_alias_decision_id is None:
            continue
        decision = _required_decision(
            rows,
            assignment.assigned_by_alias_decision_id,
        )
        proposal_ids_by_entity[assignment.entity_id].add(
            decision.alias_proposal_id
        )

    items: list[EntityRegistryAuditItem] = []
    for entity in rows.entities:
        for proposal_id in sorted(
                proposal_ids_by_entity.get(entity.id, set())
        ):
            proposal = rows.proposals.get(proposal_id)
            history = rows.histories.get(proposal_id)
            if proposal is None or not history:
                raise ValueError(
                    "Entity registry references incomplete alias history."
                )
            applied = tuple(
                sorted(
                    evidence_by_link.get(
                        (entity.id, proposal_id),
                        (),
                    ),
                    key=lambda item: (item.revision, item.id),
                )
            )
            latest = history[-1]
            validity = _validity(
                latest=latest,
                applied_decisions=applied,
            )
            items.append(
                EntityRegistryAuditItem(
                    entity_id=entity.id,
                    entity_type=entity.entity_type,
                    canonical_name=entity.canonical_name,
                    safe_for_downstream_use=False,
                    proposal_id=proposal.id,
                    left_candidate_id=(
                        proposal.left_entity_candidate_id
                    ),
                    right_candidate_id=(
                        proposal.right_entity_candidate_id
                    ),
                    applied_decision_ids=tuple(
                        decision.id for decision in applied
                    ),
                    latest_decision_id=latest.id,
                    latest_revision=latest.revision,
                    latest_status=latest.status,
                    validity=validity,
                )
            )
    return tuple(items)


def _required_decision(
        rows: _RegistryRows,
        decision_id: int,
) -> AliasDecision:
    decision = rows.decisions.get(decision_id)
    if decision is None:
        raise ValueError(
            "Entity registry references a missing alias decision."
        )
    return decision


def _build_candidate_items(
        rows: _RegistryRows,
) -> tuple[CandidateResolutionAuditItem, ...]:
    evidence_by_link: dict[
        tuple[int, int],
        list[CandidateResolutionDecision],
    ] = defaultdict(list)
    assigned_pairs = {
        (assignment.entity_id, assignment.entity_candidate_id)
        for assignment in rows.assignments
    }
    candidate_ids_by_entity: dict[int, set[int]] = defaultdict(set)

    for evidence in rows.candidate_evidence:
        decision = rows.candidate_decisions.get(
            evidence.candidate_resolution_decision_id
        )
        if decision is None:
            raise ValueError(
                "Entity registry references a missing candidate decision."
            )
        if decision.entity_id != evidence.entity_id:
            raise ValueError(
                "Candidate evidence conflicts with its target entity."
            )
        link = (
            evidence.entity_id,
            decision.seed_entity_candidate_id,
        )
        evidence_by_link[link].append(decision)
        candidate_ids_by_entity[evidence.entity_id].add(
            decision.seed_entity_candidate_id
        )

    for assignment in rows.assignments:
        decision_id = (
            assignment.assigned_by_candidate_resolution_decision_id
        )
        if decision_id is None:
            continue
        decision = rows.candidate_decisions.get(decision_id)
        if decision is None:
            raise ValueError(
                "Candidate assignment references a missing decision."
            )
        if decision.entity_id != assignment.entity_id:
            raise ValueError(
                "Candidate assignment conflicts with its decision entity."
            )
        seed = rows.candidates.get(
            decision.seed_entity_candidate_id
        )
        assigned_candidate = rows.candidates.get(
            assignment.entity_candidate_id
        )
        if seed is None or assigned_candidate is None:
            raise ValueError(
                "Candidate assignment references a missing candidate."
            )
        if (
                decision.scope is CandidateResolutionScope.SINGLE
                and assigned_candidate.id != seed.id
        ):
            raise ValueError(
                "Single-candidate decision assigned another candidate."
            )
        if (
                decision.scope
                is CandidateResolutionScope.EXACT_CANONICAL
                and (
                    assigned_candidate.entity_type
                    is not seed.entity_type
                    or assigned_candidate.canonical_text
                    != seed.canonical_text
                )
        ):
            raise ValueError(
                "Exact-canonical decision assigned a non-matching "
                "candidate."
            )
        candidate_ids_by_entity[assignment.entity_id].add(
            decision.seed_entity_candidate_id
        )

    items: list[CandidateResolutionAuditItem] = []
    for entity in rows.entities:
        for candidate_id in sorted(
                candidate_ids_by_entity.get(entity.id, set())
        ):
            seed = rows.candidates.get(candidate_id)
            history = rows.candidate_histories.get(candidate_id)
            if seed is None or not history:
                raise ValueError(
                    "Entity registry references incomplete candidate "
                    "resolution history."
                )
            if seed.entity_type is not entity.entity_type:
                raise ValueError(
                    "Candidate resolution type conflicts with its entity."
                )
            applied = tuple(
                sorted(
                    evidence_by_link.get(
                        (entity.id, candidate_id),
                        (),
                    ),
                    key=lambda item: (item.revision, item.id),
                )
            )
            latest = history[-1]
            validity = _candidate_validity(
                latest=latest,
                applied_decisions=applied,
                seed_is_assigned=(
                    (entity.id, seed.id) in assigned_pairs
                ),
            )
            items.append(
                CandidateResolutionAuditItem(
                    entity_id=entity.id,
                    entity_type=entity.entity_type,
                    canonical_name=entity.canonical_name,
                    safe_for_downstream_use=False,
                    seed_candidate_id=seed.id,
                    seed_canonical_text=seed.canonical_text,
                    scope=latest.scope,
                    applied_decision_ids=tuple(
                        decision.id for decision in applied
                    ),
                    latest_decision_id=latest.id,
                    latest_revision=latest.revision,
                    latest_status=latest.status,
                    validity=validity,
                )
            )
    return tuple(items)


def _validity(
        *,
        latest: AliasDecision,
        applied_decisions: tuple[AliasDecision, ...],
) -> EntityResolutionValidity:
    if latest.status is AliasDecisionStatus.REJECTED:
        return EntityResolutionValidity.REVOKED
    if latest.status is AliasDecisionStatus.NEEDS_REVIEW:
        return EntityResolutionValidity.NEEDS_REVIEW
    if any(decision.id == latest.id for decision in applied_decisions):
        return EntityResolutionValidity.ACTIVE
    return EntityResolutionValidity.PENDING_REAPPLICATION


def _candidate_validity(
        *,
        latest: CandidateResolutionDecision,
        applied_decisions: tuple[CandidateResolutionDecision, ...],
        seed_is_assigned: bool,
) -> EntityResolutionValidity:
    if latest.status is CandidateResolutionStatus.REVOKED:
        return EntityResolutionValidity.REVOKED
    if (
            seed_is_assigned
            and any(
                decision.id == latest.id
                for decision in applied_decisions
            )
    ):
        return EntityResolutionValidity.ACTIVE
    return EntityResolutionValidity.PENDING_REAPPLICATION


def _group_by_entity(
        items: tuple[EntityRegistryAuditItem, ...],
) -> dict[int, list[EntityRegistryAuditItem]]:
    grouped: dict[int, list[EntityRegistryAuditItem]] = defaultdict(list)
    for item in items:
        grouped[item.entity_id].append(item)
    return grouped


def _group_candidate_items_by_entity(
        items: tuple[CandidateResolutionAuditItem, ...],
) -> dict[int, list[CandidateResolutionAuditItem]]:
    grouped: dict[int, list[CandidateResolutionAuditItem]] = defaultdict(list)
    for item in items:
        grouped[item.entity_id].append(item)
    return grouped


def _with_entity_safety(
        item: EntityRegistryAuditItem,
        *,
        safe_for_downstream_use: bool,
) -> EntityRegistryAuditItem:
    return EntityRegistryAuditItem(
        entity_id=item.entity_id,
        entity_type=item.entity_type,
        canonical_name=item.canonical_name,
        safe_for_downstream_use=safe_for_downstream_use,
        proposal_id=item.proposal_id,
        left_candidate_id=item.left_candidate_id,
        right_candidate_id=item.right_candidate_id,
        applied_decision_ids=item.applied_decision_ids,
        latest_decision_id=item.latest_decision_id,
        latest_revision=item.latest_revision,
        latest_status=item.latest_status,
        validity=item.validity,
    )


def _with_candidate_entity_safety(
        item: CandidateResolutionAuditItem,
        *,
        safe_for_downstream_use: bool,
) -> CandidateResolutionAuditItem:
    return CandidateResolutionAuditItem(
        entity_id=item.entity_id,
        entity_type=item.entity_type,
        canonical_name=item.canonical_name,
        safe_for_downstream_use=safe_for_downstream_use,
        seed_candidate_id=item.seed_candidate_id,
        seed_canonical_text=item.seed_canonical_text,
        scope=item.scope,
        applied_decision_ids=item.applied_decision_ids,
        latest_decision_id=item.latest_decision_id,
        latest_revision=item.latest_revision,
        latest_status=item.latest_status,
        validity=item.validity,
    )
