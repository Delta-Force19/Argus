from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from sqlalchemy.orm import Mapped, mapped_column

from argus.acquisition.contracts import RetrievalOutcome
from argus.analysis_runs import AnalysisRunStatus
from argus.database import Base
from argus.documents import DerivedArtifactType, DocumentType
from argus.endpoints import EndpointType
from argus.knowledge import (
    AliasDecisionStatus,
    AliasSignalType,
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
)

from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)

from argus.sources import SourceType
from argus.telegram_subscriptions import TelegramSubscriberStatus


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    identifier: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    source_type: Mapped[SourceType] = mapped_column(
        SQLAlchemyEnum(
            SourceType,
            name="source_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        default=SourceType.NEWS_MEDIA,
        index=True,
    )

    primary_jurisdiction: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    default_language: Mapped[str | None] = mapped_column(
        String(35),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class CollectionEndpoint(Base):
    __tablename__ = "collection_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "connector_id",
            "url",
            name="uq_collection_endpoint_connector_url",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    identifier: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    source_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "sources.id",
            name=(
                "fk_collection_endpoints_source_id_sources"
            ),
        ),
        nullable=True,
        index=True,
    )

    endpoint_type: Mapped[EndpointType] = mapped_column(
        SQLAlchemyEnum(
            EndpointType,
            name="endpoint_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )

    connector_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    language: Mapped[str | None] = mapped_column(
        String(35),
        nullable=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"
    __table_args__ = (
        CheckConstraint(
            "byte_size >= 0",
            name="ck_raw_artifacts_byte_size_non_negative",
        ),
        UniqueConstraint(
            "hash_algorithm",
            "content_hash",
            name="uq_raw_artifact_digest",
        ),
        UniqueConstraint(
            "storage_backend",
            "storage_key",
            name="uq_raw_artifact_storage_location",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    hash_algorithm: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    byte_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    storage_backend: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    storage_key: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "identifier_scheme",
            "identifier_value",
            name="uq_document_stable_identity",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    source_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "sources.id",
            name="fk_documents_source_id_sources",
        ),
        nullable=True,
        index=True,
    )

    document_type: Mapped[DocumentType] = mapped_column(
        SQLAlchemyEnum(
            DocumentType,
            name="document_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )

    identifier_scheme: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    identifier_value: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    language: Mapped[str | None] = mapped_column(
        String(35),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        CheckConstraint(
            "version_number >= 1",
            name="ck_document_versions_number_positive",
        ),
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_version_number",
        ),
        UniqueConstraint(
            "document_id",
            "raw_artifact_id",
            name="uq_document_version_artifact",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "documents.id",
            name=(
                "fk_document_versions_document_id_documents"
            ),
        ),
        nullable=False,
        index=True,
    )

    raw_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "raw_artifacts.id",
            name=(
                "fk_document_versions_raw_artifact_id_"
                "raw_artifacts"
            ),
        ),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    media_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class DerivedArtifact(Base):
    __tablename__ = "derived_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "artifact_type",
            "method",
            "method_version",
            "schema_version",
            "content_hash",
            name="uq_derived_artifact_reproducible_output",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_derived_artifacts_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=False,
        index=True,
    )

    artifact_type: Mapped[DerivedArtifactType] = mapped_column(
        SQLAlchemyEnum(
            DerivedArtifactType,
            name="derived_artifact_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )

    method: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    method_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False
    )
    quality_limitations: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )


class EntityMention(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        CheckConstraint(
            "start_char >= 0",
            name="ck_entity_mentions_start_non_negative",
        ),
        CheckConstraint(
            "end_char > start_char",
            name="ck_entity_mentions_end_after_start",
        ),
        UniqueConstraint(
            "derived_artifact_id",
            "start_char",
            "end_char",
            "source_label",
            name="uq_entity_mention_artifact_span_label",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    derived_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "derived_artifacts.id",
            name=(
                "fk_entity_mentions_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_entity_mentions_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        SQLAlchemyEnum(
            EntityType,
            name="entity_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    source_label: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    surface_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class EntityCandidate(Base):
    __tablename__ = "entity_candidates"
    __table_args__ = (
        CheckConstraint(
            "context_start_char >= 0",
            name="ck_entity_candidates_context_start_non_negative",
        ),
        CheckConstraint(
            "context_end_char > context_start_char",
            name="ck_entity_candidates_context_end_after_start",
        ),
        UniqueConstraint(
            "derived_artifact_id",
            "entity_mention_id",
            name="uq_entity_candidate_artifact_mention",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    derived_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "derived_artifacts.id",
            name=(
                "fk_entity_candidates_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_mention_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_mentions.id",
            name=(
                "fk_entity_candidates_entity_mention_id_"
                "entity_mentions"
            ),
        ),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_entity_candidates_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        SQLAlchemyEnum(
            EntityType,
            name="entity_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    canonical_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )
    context_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_start_char: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    context_end_char: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class AliasProposal(Base):
    __tablename__ = "alias_proposals"
    __table_args__ = (
        CheckConstraint(
            "right_entity_candidate_id > left_entity_candidate_id",
            name="ck_alias_proposals_candidate_order",
        ),
        CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_alias_proposals_confidence_range",
        ),
        CheckConstraint(
            "left_occurrence_count >= 1",
            name="ck_alias_proposals_left_count_positive",
        ),
        CheckConstraint(
            "right_occurrence_count >= 1",
            name="ck_alias_proposals_right_count_positive",
        ),
        CheckConstraint(
            "shared_document_count >= 1",
            name="ck_alias_proposals_shared_documents_positive",
        ),
        UniqueConstraint(
            "derived_artifact_id",
            "left_entity_candidate_id",
            "right_entity_candidate_id",
            "signal_type",
            name="uq_alias_proposal_artifact_pair_signal",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    derived_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "derived_artifacts.id",
            name=(
                "fk_alias_proposals_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_alias_proposals_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=False,
        index=True,
    )
    left_entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_alias_proposals_left_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    right_entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_alias_proposals_right_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        SQLAlchemyEnum(
            EntityType,
            name="entity_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    left_canonical_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )
    right_canonical_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )
    signal_type: Mapped[AliasSignalType] = mapped_column(
        SQLAlchemyEnum(
            AliasSignalType,
            name="alias_signal_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    confidence_basis: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    left_occurrence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    right_occurrence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    shared_document_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class AliasDecision(Base):
    __tablename__ = "alias_decisions"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_alias_decisions_revision_positive",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_alias_decisions_reason_not_blank",
        ),
        CheckConstraint(
            "length(trim(reviewer)) > 0",
            name="ck_alias_decisions_reviewer_not_blank",
        ),
        UniqueConstraint(
            "alias_proposal_id",
            "revision",
            name="uq_alias_decision_proposal_revision",
        ),
        UniqueConstraint(
            "supersedes_alias_decision_id",
            name="uq_alias_decision_supersedes",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias_proposal_id: Mapped[int] = mapped_column(
        ForeignKey(
            "alias_proposals.id",
            name=(
                "fk_alias_decisions_alias_proposal_id_"
                "alias_proposals"
            ),
        ),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_alias_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "alias_decisions.id",
            name=(
                "fk_alias_decisions_supersedes_id_"
                "alias_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    status: Mapped[AliasDecisionStatus] = mapped_column(
        SQLAlchemyEnum(
            AliasDecisionStatus,
            name="alias_decision_status",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class Entity(Base):
    """One persistent identity established through explicit resolution."""

    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(
            "length(trim(canonical_name)) > 0",
            name="ck_entities_canonical_name_not_blank",
        ),
        UniqueConstraint(
            "canonical_entity_candidate_id",
            name="uq_entities_canonical_candidate",
        ),
        UniqueConstraint(
            "created_from_alias_decision_id",
            name="uq_entities_creation_decision",
        ),
        UniqueConstraint(
            "created_from_candidate_resolution_decision_id",
            name="uq_entities_candidate_creation_decision",
        ),
        CheckConstraint(
            "("
            "created_from_alias_decision_id IS NOT NULL "
            "AND created_from_candidate_resolution_decision_id IS NULL"
            ") OR ("
            "created_from_alias_decision_id IS NULL "
            "AND created_from_candidate_resolution_decision_id IS NOT NULL"
            ")",
            name="ck_entities_exactly_one_creation_decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[EntityType] = mapped_column(
        SQLAlchemyEnum(
            EntityType,
            name="entity_type",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    canonical_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )
    canonical_entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_entities_canonical_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    created_from_alias_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "alias_decisions.id",
            name=(
                "fk_entities_creation_decision_id_"
                "alias_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    created_from_candidate_resolution_decision_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "candidate_resolution_decisions.id",
            name=(
                "fk_entities_candidate_creation_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class EntityCandidateAssignment(Base):
    """Auditable assignment of one candidate observation to an entity."""

    __tablename__ = "entity_candidate_assignments"
    __table_args__ = (
        UniqueConstraint(
            "entity_candidate_id",
            name="uq_entity_candidate_assignments_candidate",
        ),
        CheckConstraint(
            "("
            "assigned_by_alias_decision_id IS NOT NULL "
            "AND assigned_by_candidate_resolution_decision_id IS NULL"
            ") OR ("
            "assigned_by_alias_decision_id IS NULL "
            "AND assigned_by_candidate_resolution_decision_id IS NOT NULL"
            ")",
            name="ck_entity_assignments_exactly_one_decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entities.id",
            name="fk_entity_candidate_assignments_entity_id_entities",
        ),
        nullable=False,
        index=True,
    )
    entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_entity_candidate_assignments_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    assigned_by_alias_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "alias_decisions.id",
            name=(
                "fk_entity_candidate_assignments_decision_id_"
                "alias_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    assigned_by_candidate_resolution_decision_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "candidate_resolution_decisions.id",
            name=(
                "fk_entity_assignments_candidate_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class EntityResolutionEvidence(Base):
    """Approved review decision used to establish or extend an entity."""

    __tablename__ = "entity_resolution_evidence"
    __table_args__ = (
        UniqueConstraint(
            "alias_decision_id",
            name="uq_entity_resolution_evidence_decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entities.id",
            name="fk_entity_resolution_evidence_entity_id_entities",
        ),
        nullable=False,
        index=True,
    )
    alias_decision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "alias_decisions.id",
            name=(
                "fk_entity_resolution_evidence_decision_id_"
                "alias_decisions"
            ),
        ),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class CandidateResolutionDecision(Base):
    """Append-only decision about one candidate and an explicit scope."""

    __tablename__ = "candidate_resolution_decisions"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_candidate_resolution_revision_positive",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_resolution_reason_not_blank",
        ),
        CheckConstraint(
            "length(trim(reviewer)) > 0",
            name="ck_candidate_resolution_reviewer_not_blank",
        ),
        UniqueConstraint(
            "seed_entity_candidate_id",
            "revision",
            name="uq_candidate_resolution_candidate_revision",
        ),
        UniqueConstraint(
            "supersedes_candidate_resolution_decision_id",
            name="uq_candidate_resolution_supersedes",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed_entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_candidate_resolution_seed_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_candidate_resolution_decision_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "candidate_resolution_decisions.id",
            name=(
                "fk_candidate_resolution_supersedes_id_"
                "candidate_resolution_decisions"
            ),
        ),
        nullable=True,
        index=True,
    )
    status: Mapped[CandidateResolutionStatus] = mapped_column(
        SQLAlchemyEnum(
            CandidateResolutionStatus,
            name="candidate_resolution_status",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    scope: Mapped[CandidateResolutionScope] = mapped_column(
        SQLAlchemyEnum(
            CandidateResolutionScope,
            name="candidate_resolution_scope",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "entities.id",
            name=(
                "fk_candidate_resolution_entity_id_entities"
            ),
        ),
        nullable=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class CandidateResolutionEvidence(Base):
    """Exact candidate-resolution revision applied to one entity."""

    __tablename__ = "candidate_resolution_evidence"
    __table_args__ = (
        UniqueConstraint(
            "candidate_resolution_decision_id",
            name="uq_candidate_resolution_evidence_decision",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entities.id",
            name=(
                "fk_candidate_resolution_evidence_entity_id_entities"
            ),
        ),
        nullable=False,
        index=True,
    )
    candidate_resolution_decision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "candidate_resolution_decisions.id",
            name=(
                "fk_candidate_resolution_evidence_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class CandidateResolutionExclusion(Base):
    """Exact candidate covered by one applied not-entity decision."""

    __tablename__ = "candidate_resolution_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_resolution_decision_id",
            "entity_candidate_id",
            name="uq_candidate_resolution_exclusion_decision_candidate",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_resolution_decision_id: Mapped[int] = mapped_column(
        ForeignKey(
            "candidate_resolution_decisions.id",
            name=(
                "fk_candidate_resolution_exclusion_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "entity_candidates.id",
            name=(
                "fk_candidate_resolution_exclusion_candidate_id_"
                "entity_candidates"
            ),
        ),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class AnalysisRun(Base):
    """Immutable prepared contract for one reproducible analysis."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_analysis_runs_input_fingerprint_sha256",
        ),
        CheckConstraint(
            "length(configuration_hash) = 64",
            name="ck_analysis_runs_configuration_hash_sha256",
        ),
        UniqueConstraint(
            "input_fingerprint",
            "analysis_method",
            "analysis_method_version",
            "software_version",
            "configuration_hash",
            name="uq_analysis_runs_reproducible_preparation",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_analysis_runs_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=False,
        index=True,
    )
    entity_type_scope: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    analysis_method: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    analysis_method_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    software_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    configuration: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )
    configuration_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    input_schema_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    input_manifest: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )
    input_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    status: Mapped[AnalysisRunStatus] = mapped_column(
        SQLAlchemyEnum(
            AnalysisRunStatus,
            name="analysis_run_status",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        default=AnalysisRunStatus.PREPARED,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class AcquisitionCandidate(Base):
    __tablename__ = "acquisition_candidates"
    __table_args__ = (
        CheckConstraint(
            "discovery_count >= 1",
            name=(
                "ck_acquisition_candidates_discovery_count_"
                "positive"
            ),
        ),
        UniqueConstraint(
            "endpoint_id",
            "fingerprint",
            name="uq_acquisition_candidate_endpoint_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collection_endpoints.id",
            name=(
                "fk_acquisition_candidates_endpoint_id_"
                "collection_endpoints"
            ),
        ),
        nullable=False,
        index=True,
    )

    article_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "articles.id",
            name=(
                "fk_acquisition_candidates_article_id_articles"
            ),
        ),
        nullable=True,
        index=True,
    )

    fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    connector_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    connector_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    external_identifier: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    location: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    source_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    media_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    language: Mapped[str | None] = mapped_column(
        String(35),
        nullable=True,
        index=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    last_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    discovery_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class RetrievalAttempt(Base):
    __tablename__ = "retrieval_attempts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey(
            "collection_endpoints.id",
            name=(
                "fk_retrieval_attempts_endpoint_id_"
                "collection_endpoints"
            ),
        ),
        nullable=False,
        index=True,
    )

    article_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "articles.id",
            name="fk_retrieval_attempts_article_id_articles",
        ),
        nullable=True,
        index=True,
    )

    raw_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "raw_artifacts.id",
            name=(
                "fk_retrieval_attempts_raw_artifact_id_"
                "raw_artifacts"
            ),
        ),
        nullable=True,
        index=True,
    )

    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "acquisition_candidates.id",
            name=(
                "fk_retrieval_attempts_candidate_id_"
                "acquisition_candidates"
            ),
        ),
        nullable=True,
        index=True,
    )

    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name=(
                "fk_retrieval_attempts_document_version_id_"
                "document_versions"
            ),
        ),
        nullable=True,
        index=True,
    )

    connector_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    connector_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    requested_location: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    external_identifier: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    outcome: Mapped[RetrievalOutcome] = mapped_column(
        SQLAlchemyEnum(
            RetrievalOutcome,
            name="retrieval_outcome",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )

    resolved_location: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    response_status: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    content_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    hash_algorithm: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    warnings: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    request_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            name="uq_articles_document_id",
        ),
        UniqueConstraint(
            "content_derived_artifact_id",
            name="uq_articles_content_derived_artifact_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    source_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "sources.id",
            name="fk_articles_source_id_sources",
        ),
        nullable=True,
        index=True,
    )

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "documents.id",
            name="fk_articles_document_id_documents",
        ),
        nullable=True,
        index=True,
    )

    content_derived_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "derived_artifacts.id",
            name=(
                "fk_articles_content_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        nullable=True,
        index=True,
    )

    url: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)

    author: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class ProcessingState(Base):
    __tablename__ = "processing_states"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "stage",
            "method_version",
            name="uq_processing_article_stage_method",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id"),
        nullable=False,
        index=True,
    )

    stage: Mapped[ProcessingStage] = mapped_column(
        SQLAlchemyEnum(
            ProcessingStage,
            name="processing_stage",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=50,
        ),
        nullable=False,
        index=True,
    )

    method_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="default",
    )

    status: Mapped[ProcessingStatus] = mapped_column(
        SQLAlchemyEnum(
            ProcessingStatus,
            name="processing_status",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=ProcessingStatus.PENDING,
        index=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class DiscourseAnalysisResult(Base):
    __tablename__ = "discourse_analysis_results"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "method_version",
            name="uq_discourse_article_method",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id"),
        nullable=False,
        index=True,
    )

    method_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    word_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    sentence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    average_sentence_length: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    question_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    exclamation_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    first_person_plural_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    third_person_plural_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    certainty_marker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    uncertainty_marker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    fear_marker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    threat_marker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AnalysisEvidence(Base):
    __tablename__ = "analysis_evidence"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    analysis_result_id: Mapped[int] = mapped_column(
        ForeignKey("discourse_analysis_results.id"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    sentence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    matched_terms: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )


class TelegramSubscriber(Base):
    """One Telegram chat authorized to use Argus reader delivery."""

    __tablename__ = "telegram_subscribers"
    __table_args__ = (
        CheckConstraint(
            (
                "last_delivered_article_id IS NULL "
                "OR last_delivered_article_id >= 0"
            ),
            name=(
                "ck_telegram_subscribers_last_article_id_non_negative"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )

    status: Mapped[TelegramSubscriberStatus] = mapped_column(
        SQLAlchemyEnum(
            TelegramSubscriberStatus,
            name="telegram_subscriber_status",
            native_enum=False,
            values_callable=lambda enum_type: [
                member.value
                for member in enum_type
            ],
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=TelegramSubscriberStatus.PENDING,
        index=True,
    )

    is_subscribed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    last_delivered_article_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
