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
    EntityMention,
)


@dataclass(frozen=True, slots=True)
class MentionCount:
    """One named bucket in an entity-mention audit."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class FrequentMention:
    """One frequent normalized form without implying entity identity."""

    entity_type: EntityType
    normalized_text: str
    mention_count: int
    document_count: int
    surface_variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MentionRunSummary:
    """Density summary for one reproducible recognition artifact."""

    artifact_id: int
    document_version_id: int
    language: str
    mention_count: int
    unique_form_count: int
    method_version: str
    title: str | None


@dataclass(frozen=True, slots=True)
class MentionExample:
    """One deterministic, offset-bearing example for manual review."""

    mention_id: int
    artifact_id: int
    document_version_id: int
    language: str
    entity_type: EntityType
    source_label: str
    surface_text: str
    normalized_text: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class EntityMentionAuditReport:
    """Read-only quality overview of persisted entity mentions."""

    mention_count: int
    artifact_count: int
    document_version_count: int
    counts_by_language: tuple[MentionCount, ...]
    counts_by_type: tuple[MentionCount, ...]
    frequent_mentions: tuple[FrequentMention, ...]
    densest_runs: tuple[MentionRunSummary, ...]
    examples: tuple[MentionExample, ...]


@dataclass(frozen=True, slots=True)
class _AuditRow:
    mention: EntityMention
    artifact: DerivedArtifact
    document: Document

    @property
    def language(self) -> str:
        payload_language = self.artifact.payload.get("language")
        if isinstance(payload_language, str) and payload_language.strip():
            return payload_language.strip().lower()
        if self.document.language and self.document.language.strip():
            return self.document.language.strip().lower().split("-", 1)[0]
        return "unknown"


@dataclass(frozen=True, slots=True)
class _AuditRun:
    artifact: DerivedArtifact
    document: Document

    @property
    def language(self) -> str:
        payload_language = self.artifact.payload.get("language")
        if isinstance(payload_language, str) and payload_language.strip():
            return payload_language.strip().lower()
        if self.document.language and self.document.language.strip():
            return self.document.language.strip().lower().split("-", 1)[0]
        return "unknown"


def get_entity_mention_audit(
        *,
        top: int = 10,
        examples: int = 10,
        session_factory: Callable[[], Session] = SessionLocal,
) -> EntityMentionAuditReport:
    """Build a deterministic report without changing persisted state."""

    if top < 1:
        raise ValueError("top must be greater than zero.")
    if examples < 1:
        raise ValueError("examples must be greater than zero.")

    with session_factory() as session:
        runs = _load_runs(session)
        rows = _load_rows(session)

    language_counts = Counter(row.language for row in rows)
    type_counts = Counter(row.mention.entity_type for row in rows)
    artifact_ids = {run.artifact.id for run in runs}
    document_version_ids = {
        run.artifact.document_version_id for run in runs
    }

    return EntityMentionAuditReport(
        mention_count=len(rows),
        artifact_count=len(artifact_ids),
        document_version_count=len(document_version_ids),
        counts_by_language=tuple(
            MentionCount(name=name, count=count)
            for name, count in sorted(language_counts.items())
        ),
        counts_by_type=tuple(
            MentionCount(name=entity_type.value, count=count)
            for entity_type, count in sorted(
                type_counts.items(),
                key=lambda item: item[0].value,
            )
        ),
        frequent_mentions=_frequent_mentions(rows, limit=top),
        densest_runs=_densest_runs(runs, rows, limit=top),
        examples=_select_examples(rows, limit=examples),
    )


def _load_rows(session: Session) -> list[_AuditRow]:
    statement = (
        select(EntityMention, DerivedArtifact, Document)
        .join(
            DerivedArtifact,
            DerivedArtifact.id == EntityMention.derived_artifact_id,
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == EntityMention.document_version_id,
        )
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ENTITY_MENTIONS
        )
        .order_by(EntityMention.id.asc())
    )
    return [
        _AuditRow(mention=mention, artifact=artifact, document=document)
        for mention, artifact, document in session.execute(statement)
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
            == DerivedArtifactType.ENTITY_MENTIONS
        )
        .order_by(DerivedArtifact.id.asc())
    )
    return [
        _AuditRun(artifact=artifact, document=document)
        for artifact, document in session.execute(statement)
    ]


def _frequent_mentions(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[FrequentMention, ...]:
    grouped: dict[
        tuple[EntityType, str],
        list[_AuditRow],
    ] = defaultdict(list)
    for row in rows:
        grouped[
            (row.mention.entity_type, row.mention.normalized_text)
        ].append(row)

    results = [
        FrequentMention(
            entity_type=entity_type,
            normalized_text=normalized_text,
            mention_count=len(group_rows),
            document_count=len({
                row.mention.document_version_id for row in group_rows
            }),
            surface_variants=tuple(sorted({
                row.mention.surface_text for row in group_rows
            })),
        )
        for (entity_type, normalized_text), group_rows in grouped.items()
    ]
    results.sort(
        key=lambda item: (
            -item.mention_count,
            -item.document_count,
            item.entity_type.value,
            item.normalized_text,
        )
    )
    return tuple(results[:limit])


def _densest_runs(
        runs: list[_AuditRun],
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[MentionRunSummary, ...]:
    grouped: dict[int, list[_AuditRow]] = defaultdict(list)
    for row in rows:
        grouped[row.artifact.id].append(row)

    results = []
    for run in runs:
        artifact_id = run.artifact.id
        group_rows = grouped[artifact_id]
        results.append(
            MentionRunSummary(
                artifact_id=artifact_id,
                document_version_id=run.artifact.document_version_id,
                language=run.language,
                mention_count=len(group_rows),
                unique_form_count=len({
                    (
                        row.mention.entity_type,
                        row.mention.normalized_text,
                    )
                    for row in group_rows
                }),
                method_version=run.artifact.method_version,
                title=run.document.title,
            )
        )
    results.sort(
        key=lambda item: (
            -item.mention_count,
            item.artifact_id,
        )
    )
    return tuple(results[:limit])


def _select_examples(
        rows: list[_AuditRow],
        *,
        limit: int,
) -> tuple[MentionExample, ...]:
    by_type: dict[EntityType, list[_AuditRow]] = defaultdict(list)
    for row in rows:
        by_type[row.mention.entity_type].append(row)

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
        MentionExample(
            mention_id=row.mention.id,
            artifact_id=row.artifact.id,
            document_version_id=row.mention.document_version_id,
            language=row.language,
            entity_type=row.mention.entity_type,
            source_label=row.mention.source_label,
            surface_text=row.mention.surface_text,
            normalized_text=row.mention.normalized_text,
            start_char=row.mention.start_char,
            end_char=row.mention.end_char,
        )
        for row in selected
    )
