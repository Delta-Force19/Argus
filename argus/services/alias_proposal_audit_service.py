from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.knowledge import AliasSignalType, EntityType
from argus.models import (
    AliasProposal,
    DerivedArtifact,
    Document,
    DocumentVersion,
    EntityCandidate,
)


@dataclass(frozen=True, slots=True)
class ProposalCount:
    """One named bucket in an alias-proposal audit."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class ProposalRunSummary:
    """Summary of one reproducible alias-proposal artifact."""

    artifact_id: int
    input_artifact_id: int | None
    document_version_id: int
    language: str
    proposal_count: int
    proposer_version: str
    title: str | None


@dataclass(frozen=True, slots=True)
class ProposalExample:
    """One proposal with the exact evidence required for review."""

    proposal_id: int
    artifact_id: int
    document_version_id: int
    language: str
    entity_type: EntityType
    left_text: str
    right_text: str
    signal_type: AliasSignalType
    confidence_score: float
    confidence_band: str
    confidence_basis: str
    rationale: str
    left_occurrence_count: int
    right_occurrence_count: int
    shared_document_count: int
    left_context: str
    right_context: str
    title: str | None


@dataclass(frozen=True, slots=True)
class AliasProposalAuditReport:
    """Read-only overview of persisted alias proposals."""

    proposal_count: int
    artifact_count: int
    document_version_count: int
    counts_by_signal: tuple[ProposalCount, ...]
    counts_by_type: tuple[ProposalCount, ...]
    counts_by_confidence_band: tuple[ProposalCount, ...]
    runs: tuple[ProposalRunSummary, ...]
    examples: tuple[ProposalExample, ...]
    quality_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AuditRun:
    artifact: DerivedArtifact
    document: Document

    @property
    def language(self) -> str:
        return _language(self.document.language)


@dataclass(frozen=True, slots=True)
class _AuditRow:
    proposal: AliasProposal
    artifact: DerivedArtifact
    left_candidate: EntityCandidate
    right_candidate: EntityCandidate
    document: Document

    @property
    def language(self) -> str:
        return _language(self.document.language)


def get_alias_proposal_audit(
        *,
        top: int = 10,
        examples: int = 20,
        session_factory: Callable[[], Session] = SessionLocal,
) -> AliasProposalAuditReport:
    """Build a deterministic proposal report without changing state."""

    if top < 1:
        raise ValueError("top must be greater than zero.")
    if examples < 1:
        raise ValueError("examples must be greater than zero.")

    with session_factory() as session:
        runs = _load_runs(session)
        rows = _load_rows(session)

    signal_counts = Counter(
        row.proposal.signal_type.value for row in rows
    )
    type_counts = Counter(
        row.proposal.entity_type.value for row in rows
    )
    band_counts = Counter(
        _confidence_band(row.proposal.confidence_score) for row in rows
    )

    return AliasProposalAuditReport(
        proposal_count=len(rows),
        artifact_count=len(runs),
        document_version_count=len({
            run.artifact.document_version_id for run in runs
        }),
        counts_by_signal=_counts(signal_counts),
        counts_by_type=_counts(type_counts),
        counts_by_confidence_band=tuple(
            ProposalCount(name=name, count=band_counts.get(name, 0))
            for name in ("high", "medium", "low")
            if band_counts.get(name, 0)
        ),
        runs=_run_summaries(runs, rows, limit=top),
        examples=_examples(rows, limit=examples),
        quality_limitations=_quality_limitations(runs),
    )


def _load_runs(session: Session) -> list[_AuditRun]:
    statement = (
        select(DerivedArtifact, Document)
        .join(
            DocumentVersion,
            DocumentVersion.id == DerivedArtifact.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ALIAS_PROPOSALS
        )
        .order_by(DerivedArtifact.id.asc())
    )
    return [
        _AuditRun(artifact=artifact, document=document)
        for artifact, document in session.execute(statement)
    ]


def _load_rows(session: Session) -> list[_AuditRow]:
    left_candidate = aliased(EntityCandidate)
    right_candidate = aliased(EntityCandidate)
    statement = (
        select(
            AliasProposal,
            DerivedArtifact,
            left_candidate,
            right_candidate,
            Document,
        )
        .join(
            DerivedArtifact,
            DerivedArtifact.id == AliasProposal.derived_artifact_id,
        )
        .join(
            left_candidate,
            left_candidate.id
            == AliasProposal.left_entity_candidate_id,
        )
        .join(
            right_candidate,
            right_candidate.id
            == AliasProposal.right_entity_candidate_id,
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == AliasProposal.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ALIAS_PROPOSALS
        )
        .order_by(AliasProposal.id.asc())
    )
    return [
        _AuditRow(
            proposal=proposal,
            artifact=artifact,
            left_candidate=left,
            right_candidate=right,
            document=document,
        )
        for proposal, artifact, left, right, document
        in session.execute(statement)
    ]


def _counts(counter: Counter[str]) -> tuple[ProposalCount, ...]:
    return tuple(
        ProposalCount(name=name, count=count)
        for name, count in sorted(counter.items())
    )


def _run_summaries(
        runs: list[_AuditRun],
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[ProposalRunSummary, ...]:
    grouped: dict[int, list[_AuditRow]] = defaultdict(list)
    for row in rows:
        grouped[row.artifact.id].append(row)

    results = []
    for run in runs:
        input_id = run.artifact.payload.get("input_artifact_id")
        results.append(
            ProposalRunSummary(
                artifact_id=run.artifact.id,
                input_artifact_id=(
                    input_id if isinstance(input_id, int) else None
                ),
                document_version_id=run.artifact.document_version_id,
                language=run.language,
                proposal_count=len(grouped[run.artifact.id]),
                proposer_version=run.artifact.method_version,
                title=run.document.title,
            )
        )
    results.sort(key=lambda item: (-item.proposal_count, item.artifact_id))
    return tuple(results[:limit])


def _examples(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[ProposalExample, ...]:
    signal_order = {
        AliasSignalType.ACRONYM: 0,
        AliasSignalType.PERSON_SHORT_NAME: 1,
        AliasSignalType.INFLECTIONAL_VARIANT: 2,
    }
    ordered = sorted(
        rows,
        key=lambda row: (
            -row.proposal.confidence_score,
            signal_order[row.proposal.signal_type],
            row.proposal.entity_type.value,
            row.proposal.left_canonical_text,
            row.proposal.right_canonical_text,
            row.proposal.id,
        ),
    )
    return tuple(
        ProposalExample(
            proposal_id=row.proposal.id,
            artifact_id=row.artifact.id,
            document_version_id=row.proposal.document_version_id,
            language=row.language,
            entity_type=row.proposal.entity_type,
            left_text=row.proposal.left_canonical_text,
            right_text=row.proposal.right_canonical_text,
            signal_type=row.proposal.signal_type,
            confidence_score=row.proposal.confidence_score,
            confidence_band=_confidence_band(
                row.proposal.confidence_score
            ),
            confidence_basis=row.proposal.confidence_basis,
            rationale=row.proposal.rationale,
            left_occurrence_count=row.proposal.left_occurrence_count,
            right_occurrence_count=row.proposal.right_occurrence_count,
            shared_document_count=row.proposal.shared_document_count,
            left_context=row.left_candidate.context_text,
            right_context=row.right_candidate.context_text,
            title=row.document.title,
        )
        for row in ordered[:limit]
    )


def _quality_limitations(
        runs: list[_AuditRun],
) -> tuple[str, ...]:
    return tuple(sorted({
        limitation
        for run in runs
        for limitation in run.artifact.quality_limitations
    }))


def _confidence_band(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.70:
        return "medium"
    return "low"


def _language(value: str | None) -> str:
    if value and value.strip():
        return value.strip().lower().split("-", 1)[0]
    return "unknown"
