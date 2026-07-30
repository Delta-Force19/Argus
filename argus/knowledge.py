from dataclasses import dataclass
from enum import Enum
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EntityType(str, Enum):
    """Normalized type of one entity mention."""

    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    GROUP = "group"
    FACILITY = "facility"
    PRODUCT = "product"
    EVENT = "event"
    WORK = "work"
    LAW = "law"
    LANGUAGE = "language"
    DATE = "date"
    TIME = "time"
    PERCENT = "percent"
    MONEY = "money"
    QUANTITY = "quantity"
    ORDINAL = "ordinal"
    CARDINAL = "cardinal"
    OTHER = "other"


class EntityCandidateExclusionReason(str, Enum):
    """Why one raw mention is not an identity-resolution candidate."""

    VALUE_OR_TEMPORAL = "value_or_temporal"
    UNSUPPORTED_TYPE = "unsupported_type"


class AliasSignalType(str, Enum):
    """Transparent heuristic that produced an alias proposal."""

    ACRONYM = "acronym"
    PERSON_SHORT_NAME = "person_short_name"
    INFLECTIONAL_VARIANT = "inflectional_variant"


class AliasDecisionStatus(str, Enum):
    """Human review outcome for one exact alias proposal."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class CandidateResolutionStatus(str, Enum):
    """Human outcome for one candidate-identity resolution."""

    ASSIGNED = "assigned"
    REVOKED = "revoked"


class CandidateResolutionScope(str, Enum):
    """Explicit set of candidates covered by one resolution."""

    SINGLE = "single"
    EXACT_CANONICAL = "exact_canonical"


@dataclass(frozen=True, slots=True)
class ManualCandidateResolutionDecision:
    """Append-only human judgment for one candidate identity."""

    status: CandidateResolutionStatus
    scope: CandidateResolutionScope
    reason: str
    reviewer: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, CandidateResolutionStatus):
            raise ValueError(
                "Candidate resolution status must be a "
                "CandidateResolutionStatus."
            )
        if not isinstance(self.scope, CandidateResolutionScope):
            raise ValueError(
                "Candidate resolution scope must be a "
                "CandidateResolutionScope."
            )
        if not self.reason.strip():
            raise ValueError(
                "Candidate resolution reason must not be blank."
            )
        if not self.reviewer.strip():
            raise ValueError(
                "Candidate resolution reviewer must not be blank."
            )
        if len(self.reviewer.strip()) > 200:
            raise ValueError(
                "Candidate resolution reviewer must not exceed "
                "200 characters."
            )


@dataclass(frozen=True, slots=True)
class ManualAliasDecision:
    """Append-only human judgment supplied to the decision service."""

    status: AliasDecisionStatus
    reason: str
    reviewer: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, AliasDecisionStatus):
            raise ValueError(
                "Alias decision status must be an AliasDecisionStatus."
            )
        if not self.reason.strip():
            raise ValueError("Alias decision reason must not be blank.")
        if not self.reviewer.strip():
            raise ValueError("Alias decision reviewer must not be blank.")
        if len(self.reviewer.strip()) > 200:
            raise ValueError(
                "Alias decision reviewer must not exceed 200 characters."
            )


@dataclass(frozen=True, slots=True)
class AliasCandidate:
    """Detached candidate input supplied to an alias proposer."""

    id: int
    document_version_id: int
    entity_type: EntityType
    canonical_text: str
    context_text: str

    def __post_init__(self) -> None:
        if self.id < 1:
            raise ValueError("Alias candidate id must be positive.")
        if self.document_version_id < 1:
            raise ValueError(
                "Alias candidate document_version_id must be positive."
            )
        if not self.canonical_text:
            raise ValueError(
                "Alias candidate canonical_text must not be empty."
            )
        if not self.context_text:
            raise ValueError(
                "Alias candidate context_text must not be empty."
            )


@dataclass(frozen=True, slots=True)
class ProposedEntityAlias:
    """Evidence-bearing suggestion that two forms may share identity."""

    left_entity_candidate_id: int
    right_entity_candidate_id: int
    document_version_id: int
    entity_type: EntityType
    left_canonical_text: str
    right_canonical_text: str
    signal_type: AliasSignalType
    confidence_score: float
    confidence_basis: str
    rationale: str
    left_occurrence_count: int
    right_occurrence_count: int
    shared_document_count: int

    def __post_init__(self) -> None:
        if self.left_entity_candidate_id < 1:
            raise ValueError(
                "left_entity_candidate_id must be positive."
            )
        if (
                self.right_entity_candidate_id
                <= self.left_entity_candidate_id
        ):
            raise ValueError(
                "right_entity_candidate_id must follow the left id."
            )
        if self.document_version_id < 1:
            raise ValueError("document_version_id must be positive.")
        if not self.left_canonical_text:
            raise ValueError("left_canonical_text must not be empty.")
        if not self.right_canonical_text:
            raise ValueError("right_canonical_text must not be empty.")
        if self.left_canonical_text == self.right_canonical_text:
            raise ValueError("Alias proposal forms must be different.")
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(
                "confidence_score must be between zero and one."
            )
        if not self.confidence_basis.strip():
            raise ValueError("confidence_basis must not be blank.")
        if not self.rationale.strip():
            raise ValueError("rationale must not be blank.")
        if self.left_occurrence_count < 1:
            raise ValueError(
                "left_occurrence_count must be positive."
            )
        if self.right_occurrence_count < 1:
            raise ValueError(
                "right_occurrence_count must be positive."
            )
        if self.shared_document_count < 1:
            raise ValueError(
                "shared_document_count must be positive."
            )


@dataclass(frozen=True, slots=True)
class EntityCandidateDecision:
    """Versioned canonicalization decision for one immutable mention."""

    is_candidate: bool
    canonical_text: str | None = None
    exclusion_reason: EntityCandidateExclusionReason | None = None

    def __post_init__(self) -> None:
        if self.is_candidate:
            if self.canonical_text is None or not self.canonical_text:
                raise ValueError(
                    "Candidate decisions require canonical text."
                )
            if self.exclusion_reason is not None:
                raise ValueError(
                    "Candidate decisions must not have an exclusion reason."
                )
            return

        if self.canonical_text is not None:
            raise ValueError(
                "Excluded decisions must not have canonical text."
            )
        if self.exclusion_reason is None:
            raise ValueError(
                "Excluded decisions require an exclusion reason."
            )


@dataclass(frozen=True, slots=True)
class CanonicalizedEntityCandidate:
    """Queryable projection of one accepted canonicalization decision."""

    entity_mention_id: int
    document_version_id: int
    entity_type: EntityType
    canonical_text: str
    context_text: str
    context_start_char: int
    context_end_char: int

    def __post_init__(self) -> None:
        if self.entity_mention_id < 1:
            raise ValueError("entity_mention_id must be positive.")
        if self.document_version_id < 1:
            raise ValueError("document_version_id must be positive.")
        if not self.canonical_text:
            raise ValueError("canonical_text must not be empty.")
        if not self.context_text:
            raise ValueError("context_text must not be empty.")
        if self.context_start_char < 0:
            raise ValueError(
                "context_start_char must not be negative."
            )
        if self.context_end_char <= self.context_start_char:
            raise ValueError(
                "context_end_char must follow context_start_char."
            )


@dataclass(frozen=True, slots=True)
class RecognizedEntityMention:
    """One model observation anchored to exact character offsets."""

    entity_type: EntityType
    source_label: str
    surface_text: str
    normalized_text: str
    start_char: int
    end_char: int

    def __post_init__(self) -> None:
        if not self.source_label.strip():
            raise ValueError("Entity source label must not be blank.")
        if not self.surface_text:
            raise ValueError("Entity surface text must not be empty.")
        if not self.normalized_text:
            raise ValueError("Entity normalized text must not be empty.")
        if self.start_char < 0:
            raise ValueError("Entity start offset must not be negative.")
        if self.end_char <= self.start_char:
            raise ValueError("Entity end offset must follow its start.")


@dataclass(frozen=True, slots=True)
class EntityRecognitionResult:
    """Entity mentions and explicit limitations from one model run."""

    mentions: tuple[RecognizedEntityMention, ...]
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not item.strip() for item in self.quality_limitations):
            raise ValueError("Quality limitations must not contain blanks.")


@runtime_checkable
class EntityRecognizer(Protocol):
    """Recognize mentions without resolving them to canonical entities."""

    @property
    def method(self) -> str:
        ...

    def method_version(self, language: str) -> str:
        ...

    def recognize(
            self,
            text: str,
            *,
            language: str,
    ) -> EntityRecognitionResult:
        ...


@runtime_checkable
class EntityCandidateCanonicalizer(Protocol):
    """Classify and canonically normalize mentions without resolving them."""

    @property
    def method(self) -> str:
        ...

    @property
    def method_version(self) -> str:
        ...

    def canonicalize(
            self,
            *,
            entity_type: EntityType,
            normalized_text: str,
    ) -> EntityCandidateDecision:
        ...


@runtime_checkable
class EntityAliasProposer(Protocol):
    """Propose reviewable form pairs without resolving entity identity."""

    @property
    def method(self) -> str:
        ...

    @property
    def method_version(self) -> str:
        ...

    def propose(
            self,
            candidates: Sequence[AliasCandidate],
    ) -> tuple[ProposedEntityAlias, ...]:
        ...
