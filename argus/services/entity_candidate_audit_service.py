from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.knowledge import EntityType
from argus.models import (
    DerivedArtifact,
    Document,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
)
from argus.proposers import DeterministicEntityAliasProposer


@dataclass(frozen=True, slots=True)
class CandidateCount:
    """One named bucket in an entity-candidate audit."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class FrequentCandidate:
    """One frequent canonical form without implying resolved identity."""

    entity_type: EntityType
    canonical_text: str
    candidate_count: int
    document_count: int
    surface_variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateRunSummary:
    """Density summary for one reproducible candidate artifact."""

    artifact_id: int
    input_artifact_id: int | None
    document_version_id: int
    language: str
    candidate_count: int
    unique_form_count: int
    method_version: str
    title: str | None


@dataclass(frozen=True, slots=True)
class CandidateExample:
    """One deterministic candidate example with source context."""

    candidate_id: int
    artifact_id: int
    mention_id: int
    document_version_id: int
    language: str
    entity_type: EntityType
    surface_text: str
    canonical_text: str
    context_text: str
    context_start_char: int
    context_end_char: int


@dataclass(frozen=True, slots=True)
class AliasSignal:
    """A review-only form pair that may refer to the same entity."""

    entity_type: EntityType
    left_text: str
    right_text: str
    reason: str
    left_count: int
    right_count: int
    shared_document_count: int
    left_context: str
    right_context: str


@dataclass(frozen=True, slots=True)
class EntityCandidateAuditReport:
    """Read-only quality overview of persisted entity candidates."""

    candidate_count: int
    artifact_count: int
    document_version_count: int
    counts_by_language: tuple[CandidateCount, ...]
    counts_by_type: tuple[CandidateCount, ...]
    frequent_candidates: tuple[FrequentCandidate, ...]
    densest_runs: tuple[CandidateRunSummary, ...]
    alias_signals: tuple[AliasSignal, ...]
    examples: tuple[CandidateExample, ...]


@dataclass(frozen=True, slots=True)
class _AuditRow:
    candidate: EntityCandidate
    mention: EntityMention
    artifact: DerivedArtifact
    document: Document

    @property
    def language(self) -> str:
        if self.document.language and self.document.language.strip():
            return self.document.language.strip().lower().split("-", 1)[0]
        return "unknown"


@dataclass(frozen=True, slots=True)
class _AuditRun:
    artifact: DerivedArtifact
    document: Document

    @property
    def language(self) -> str:
        if self.document.language and self.document.language.strip():
            return self.document.language.strip().lower().split("-", 1)[0]
        return "unknown"


def get_entity_candidate_audit(
        *,
        top: int = 10,
        examples: int = 10,
        pairs: int = 10,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EntityCandidateAuditReport:
    """Build a deterministic candidate report without changing state."""

    if top < 1:
        raise ValueError("top must be greater than zero.")
    if examples < 1:
        raise ValueError("examples must be greater than zero.")
    if pairs < 1:
        raise ValueError("pairs must be greater than zero.")

    with session_factory() as session:
        runs = _load_runs(session)
        rows = _load_rows(session)

    language_counts = Counter(row.language for row in rows)
    type_counts = Counter(row.candidate.entity_type for row in rows)

    return EntityCandidateAuditReport(
        candidate_count=len(rows),
        artifact_count=len(runs),
        document_version_count=len({
            run.artifact.document_version_id for run in runs
        }),
        counts_by_language=tuple(
            CandidateCount(name=name, count=count)
            for name, count in sorted(language_counts.items())
        ),
        counts_by_type=tuple(
            CandidateCount(name=entity_type.value, count=count)
            for entity_type, count in sorted(
                type_counts.items(),
                key=lambda item: item[0].value,
            )
        ),
        frequent_candidates=_frequent_candidates(rows, limit=top),
        densest_runs=_densest_runs(runs, rows, limit=top),
        alias_signals=_alias_signals(rows, limit=pairs),
        examples=_select_examples(rows, limit=examples),
    )


def _load_rows(session: Session) -> list[_AuditRow]:
    statement = (
        select(
            EntityCandidate,
            EntityMention,
            DerivedArtifact,
            Document,
        )
        .join(
            EntityMention,
            EntityMention.id == EntityCandidate.entity_mention_id,
        )
        .join(
            DerivedArtifact,
            DerivedArtifact.id == EntityCandidate.derived_artifact_id,
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == EntityCandidate.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ENTITY_CANDIDATES
        )
        .order_by(EntityCandidate.id.asc())
    )
    return [
        _AuditRow(
            candidate=candidate,
            mention=mention,
            artifact=artifact,
            document=document,
        )
        for candidate, mention, artifact, document
        in session.execute(statement)
    ]


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
            == DerivedArtifactType.ENTITY_CANDIDATES
        )
        .order_by(DerivedArtifact.id.asc())
    )
    return [
        _AuditRun(artifact=artifact, document=document)
        for artifact, document in session.execute(statement)
    ]


def _frequent_candidates(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[FrequentCandidate, ...]:
    grouped = _group_forms(rows)
    results = [
        FrequentCandidate(
            entity_type=entity_type,
            canonical_text=canonical_text,
            candidate_count=len(group_rows),
            document_count=len({
                row.candidate.document_version_id for row in group_rows
            }),
            surface_variants=tuple(sorted({
                row.mention.surface_text for row in group_rows
            })),
        )
        for (entity_type, canonical_text), group_rows in grouped.items()
    ]
    results.sort(
        key=lambda item: (
            -item.candidate_count,
            -item.document_count,
            item.entity_type.value,
            item.canonical_text,
        )
    )
    return tuple(results[:limit])


def _densest_runs(
        runs: list[_AuditRun],
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[CandidateRunSummary, ...]:
    grouped: dict[int, list[_AuditRow]] = defaultdict(list)
    for row in rows:
        grouped[row.artifact.id].append(row)

    results = []
    for run in runs:
        artifact = run.artifact
        group_rows = grouped[artifact.id]
        input_id = artifact.payload.get("input_artifact_id")
        results.append(
            CandidateRunSummary(
                artifact_id=artifact.id,
                input_artifact_id=(
                    input_id if isinstance(input_id, int) else None
                ),
                document_version_id=artifact.document_version_id,
                language=run.language,
                candidate_count=len(group_rows),
                unique_form_count=len({
                    (
                        row.candidate.entity_type,
                        row.candidate.canonical_text,
                    )
                    for row in group_rows
                }),
                method_version=artifact.method_version,
                title=run.document.title,
            )
        )
    results.sort(
        key=lambda item: (-item.candidate_count, item.artifact_id)
    )
    return tuple(results[:limit])


def _alias_signals(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[AliasSignal, ...]:
    grouped = _group_forms(rows)
    by_type: dict[EntityType, list[str]] = defaultdict(list)
    for entity_type, canonical_text in grouped:
        by_type[entity_type].append(canonical_text)

    results: list[AliasSignal] = []
    for entity_type, forms in by_type.items():
        ordered_forms = sorted(forms)
        for index, left in enumerate(ordered_forms):
            for right in ordered_forms[index + 1:]:
                reason = _alias_reason(entity_type, left, right)
                if reason is None:
                    continue
                left_rows = grouped[(entity_type, left)]
                right_rows = grouped[(entity_type, right)]
                left_documents = {
                    row.candidate.document_version_id for row in left_rows
                }
                right_documents = {
                    row.candidate.document_version_id for row in right_rows
                }
                results.append(
                    AliasSignal(
                        entity_type=entity_type,
                        left_text=left,
                        right_text=right,
                        reason=reason,
                        left_count=len(left_rows),
                        right_count=len(right_rows),
                        shared_document_count=len(
                            left_documents & right_documents
                        ),
                        left_context=left_rows[0].candidate.context_text,
                        right_context=right_rows[0].candidate.context_text,
                    )
                )

    reason_order = {
        "acronym": 0,
        "person_short_name": 1,
        "inflectional_variant": 2,
    }
    results.sort(
        key=lambda item: (
            reason_order[item.reason],
            -item.shared_document_count,
            -(item.left_count + item.right_count),
            item.entity_type.value,
            item.left_text,
            item.right_text,
        )
    )
    return tuple(results[:limit])


def _alias_reason(
        entity_type: EntityType,
        left: str,
        right: str,
) -> str | None:
    signal = DeterministicEntityAliasProposer.classify_signal(
        entity_type,
        left,
        right,
    )
    return signal.value if signal is not None else None


def _select_examples(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[CandidateExample, ...]:
    by_type: dict[EntityType, list[_AuditRow]] = defaultdict(list)
    for row in rows:
        by_type[row.candidate.entity_type].append(row)

    selected: list[_AuditRow] = []
    indexes = {entity_type: 0 for entity_type in by_type}
    ordered_types = sorted(by_type, key=lambda item: item.value)

    while len(selected) < limit:
        added = False
        for entity_type in ordered_types:
            index = indexes[entity_type]
            candidates = by_type[entity_type]
            if index >= len(candidates):
                continue
            selected.append(candidates[index])
            indexes[entity_type] += 1
            added = True
            if len(selected) == limit:
                break
        if not added:
            break

    return tuple(
        CandidateExample(
            candidate_id=row.candidate.id,
            artifact_id=row.artifact.id,
            mention_id=row.mention.id,
            document_version_id=row.candidate.document_version_id,
            language=row.language,
            entity_type=row.candidate.entity_type,
            surface_text=row.mention.surface_text,
            canonical_text=row.candidate.canonical_text,
            context_text=row.candidate.context_text,
            context_start_char=row.candidate.context_start_char,
            context_end_char=row.candidate.context_end_char,
        )
        for row in selected
    )


def _group_forms(
        rows: list[_AuditRow],
) -> dict[tuple[EntityType, str], list[_AuditRow]]:
    grouped: dict[tuple[EntityType, str], list[_AuditRow]] = defaultdict(list)
    for row in rows:
        grouped[
            (row.candidate.entity_type, row.candidate.canonical_text)
        ].append(row)
    return grouped
