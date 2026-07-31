from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import EntityType
from argus.models import (
    Document,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
    RawArtifact,
)
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageItem,
    DocumentEntityCoverageStatus,
    evaluate_document_entity_coverage,
)
from argus.services.document_entity_projection_service import (
    DocumentEntityProjection,
    project_document_entities,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
    evaluate_document_entity_readiness,
)
from argus.services.entity_candidate_provenance_service import (
    EntityCandidateProvenance,
    resolve_entity_candidate_provenance,
)
from argus.services.entity_registry_audit_service import (
    evaluate_entity_registry_validity,
)


@dataclass(frozen=True, slots=True)
class AnalysisInputDocument:
    """Detached stable identity and immutable version metadata."""

    document_id: int
    document_version_id: int
    version_number: int
    document_type: DocumentType
    identifier_scheme: str
    identifier_value: str
    title: str | None
    language: str | None
    source_id: int | None
    raw_artifact_id: int
    raw_content_hash: str
    raw_hash_algorithm: str
    media_type: str | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class AnalysisInputText:
    """Exact immutable text input shared by all entity candidates."""

    derived_artifact_id: int
    artifact_type: DerivedArtifactType
    method: str
    method_version: str
    schema_version: str
    content_hash: str
    text: str
    character_count: int
    quality_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentAnalysisInputBundle:
    """Atomic fail-closed input for entity-dependent document analysis."""

    entity_type: EntityType | None
    document: AnalysisInputDocument
    text: AnalysisInputText
    readiness: DocumentEntityReadinessReport
    entities: DocumentEntityProjection
    not_entity_resolutions: tuple["AnalysisInputNotEntity", ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisInputNotEntity:
    """One provenance-checked false-positive NER observation."""

    entity_candidate_id: int
    entity_mention_id: int
    derived_artifact_id: int
    entity_type: EntityType
    canonical_text: str
    surface_text: str
    normalized_text: str
    start_char: int
    end_char: int
    decision_id: int
    revision: int
    scope: str
    reason: str
    reviewer: str


def get_document_analysis_input(
        *,
        document_version_id: int,
        entity_type: EntityType | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
) -> DocumentAnalysisInputBundle:
    """Read a ready document, its text and entities in one DB snapshot."""

    with session_factory() as session:
        return build_document_analysis_input(
            session,
            document_version_id=document_version_id,
            entity_type=entity_type,
        )


def build_document_analysis_input(
        session: Session,
        *,
        document_version_id: int,
        entity_type: EntityType | None = None,
) -> DocumentAnalysisInputBundle:
    """Build one bundle inside a caller-owned transaction snapshot."""

    if document_version_id < 1:
        raise ValueError(
            "document_version_id must be greater than zero."
        )

    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(
            "Document version does not exist: "
            f"{document_version_id}."
        )
    document = session.get(Document, version.document_id)
    if document is None:
        raise ValueError("Document version references a missing document.")
    raw_artifact = session.get(RawArtifact, version.raw_artifact_id)
    if raw_artifact is None:
        raise ValueError(
            "Document version references a missing raw artifact."
        )

    validity = evaluate_entity_registry_validity(session)
    coverage = evaluate_document_entity_coverage(
        session,
        document_version=version,
        validity=validity,
        limit=2**31 - 1,
        entity_type=entity_type,
    )
    readiness = evaluate_document_entity_readiness(
        coverage,
        entity_type=entity_type,
    )
    _require_ready(readiness)

    entities = project_document_entities(
        session,
        document_version=version,
        validity=validity,
        limit=None,
        entity_type=entity_type,
    )
    _validate_projection(readiness, entities)
    not_entity_resolutions = _not_entity_inputs(coverage.items)
    text = _load_unique_text_input(
        session,
        document_version_id=version.id,
        entity_type=entity_type,
        expected_candidate_count=readiness.candidate_count,
    )

    return DocumentAnalysisInputBundle(
        entity_type=entity_type,
        document=_document_input(document, version, raw_artifact),
        text=text,
        readiness=readiness,
        entities=entities,
        not_entity_resolutions=not_entity_resolutions,
    )


def _require_ready(report: DocumentEntityReadinessReport) -> None:
    if (
        report.status is not DocumentEntityReadinessStatus.READY
        or not report.ready_for_downstream_use
        or report.candidate_count < 1
        or (
            report.safe_resolved_count + report.not_entity_count
            != report.candidate_count
        )
        or report.unassigned_count
        or report.blocked_count
        or report.invalid_provenance_count
    ):
        raise ValueError(
            "Document entity resolution is not ready for analysis input: "
            f"document_version_id={report.document_version_id} "
            f"status={report.status.value}."
        )


def _not_entity_inputs(
        items: tuple[DocumentEntityCoverageItem, ...],
) -> tuple[AnalysisInputNotEntity, ...]:
    result: list[AnalysisInputNotEntity] = []
    for item in items:
        if item.status is not DocumentEntityCoverageStatus.NOT_ENTITY:
            continue
        required = (
            item.surface_text,
            item.normalized_text,
            item.start_char,
            item.end_char,
            item.not_entity_decision_id,
            item.not_entity_revision,
            item.not_entity_scope,
            item.not_entity_reason,
            item.not_entity_reviewer,
        )
        if any(value is None for value in required):
            raise ValueError(
                "Not-entity analysis input is missing decision provenance."
            )
        result.append(
            AnalysisInputNotEntity(
                entity_candidate_id=item.entity_candidate_id,
                entity_mention_id=item.entity_mention_id,
                derived_artifact_id=item.derived_artifact_id,
                entity_type=item.entity_type,
                canonical_text=item.canonical_text,
                surface_text=item.surface_text,
                normalized_text=item.normalized_text,
                start_char=item.start_char,
                end_char=item.end_char,
                decision_id=item.not_entity_decision_id,
                revision=item.not_entity_revision,
                scope=item.not_entity_scope,
                reason=item.not_entity_reason,
                reviewer=item.not_entity_reviewer,
            )
        )
    return tuple(result)


def _validate_projection(
        readiness: DocumentEntityReadinessReport,
        projection: DocumentEntityProjection,
) -> None:
    if (
        projection.document_version_id != readiness.document_version_id
        or projection.document_id != readiness.document_id
        or projection.version_number != readiness.version_number
        or projection.resolved_occurrence_count
        != readiness.safe_resolved_count
        or len(projection.items) != projection.resolved_entity_count
    ):
        raise ValueError(
            "Document entity projection conflicts with readiness."
        )


def _load_unique_text_input(
        session: Session,
        *,
        document_version_id: int,
        entity_type: EntityType | None,
        expected_candidate_count: int,
) -> AnalysisInputText:
    statement = (
        select(EntityCandidate, EntityMention)
        .join(
            EntityMention,
            EntityMention.id == EntityCandidate.entity_mention_id,
        )
        .where(
            EntityCandidate.document_version_id == document_version_id
        )
        .order_by(EntityCandidate.id.asc())
    )
    if entity_type is not None:
        statement = statement.where(
            EntityCandidate.entity_type == entity_type
        )
    rows = tuple(session.execute(statement).all())
    if len(rows) != expected_candidate_count:
        raise ValueError(
            "Analysis input candidate count conflicts with readiness."
        )

    provenances: list[EntityCandidateProvenance] = []
    for candidate, mention in rows:
        provenance, issue = resolve_entity_candidate_provenance(
            session,
            candidate=candidate,
            mention=mention,
            document_version_id=document_version_id,
        )
        if provenance is None or issue is not None:
            raise ValueError(
                "Analysis input contains invalid candidate provenance: "
                f"{issue or 'unknown issue'}"
            )
        provenances.append(provenance)

    text_artifact_ids = {
        item.text_artifact.id for item in provenances
    }
    if len(text_artifact_ids) != 1:
        raise ValueError(
            "Analysis input candidates do not share one text artifact."
        )
    provenance = provenances[0]
    artifact = provenance.text_artifact
    recorded_character_count = artifact.payload.get("character_count")
    if recorded_character_count is not None and (
        not isinstance(recorded_character_count, int)
        or isinstance(recorded_character_count, bool)
        or recorded_character_count != len(provenance.text)
    ):
        raise ValueError(
            "Analysis input text character count is inconsistent."
        )
    character_count = len(provenance.text)

    return AnalysisInputText(
        derived_artifact_id=artifact.id,
        artifact_type=artifact.artifact_type,
        method=artifact.method,
        method_version=artifact.method_version,
        schema_version=artifact.schema_version,
        content_hash=artifact.content_hash,
        text=provenance.text,
        character_count=character_count,
        quality_limitations=tuple(artifact.quality_limitations),
    )


def _document_input(
        document: Document,
        version: DocumentVersion,
        raw_artifact: RawArtifact,
) -> AnalysisInputDocument:
    return AnalysisInputDocument(
        document_id=document.id,
        document_version_id=version.id,
        version_number=version.version_number,
        document_type=document.document_type,
        identifier_scheme=document.identifier_scheme,
        identifier_value=document.identifier_value,
        title=document.title,
        language=document.language,
        source_id=document.source_id,
        raw_artifact_id=raw_artifact.id,
        raw_content_hash=raw_artifact.content_hash,
        raw_hash_algorithm=raw_artifact.hash_algorithm,
        media_type=version.media_type,
        published_at=version.published_at,
    )
