from enum import Enum


class DocumentType(str, Enum):
    """High-level kind of an attributable information object."""

    ARTICLE = "article"
    REPORT = "report"
    LAW = "law"
    SPEECH = "speech"
    SCIENTIFIC_WORK = "scientific_work"
    DATASET = "dataset"
    HISTORICAL_RECORD = "historical_record"
    OTHER = "other"


class DerivedArtifactType(str, Enum):
    """Kind of reproducible output derived from a document version."""

    EXTRACTED_TEXT = "extracted_text"
    OCR_TEXT = "ocr_text"
    TRANSCRIPT = "transcript"
    TRANSLATION = "translation"
    NORMALIZED_METADATA = "normalized_metadata"
    PARSED_TABLE = "parsed_table"
    CONVERTED_DOCUMENT = "converted_document"
    ENTITY_MENTIONS = "entity_mentions"
    ENTITY_CANDIDATES = "entity_candidates"
    ALIAS_PROPOSALS = "alias_proposals"
    EVENT_OBSERVATIONS = "event_observations"
    EVENT_FRAGMENT_PROFILES = "event_fragment_profiles"
    OTHER = "other"


class DocumentIdentityConflict(ValueError):
    """Raised when a stable document identity conflicts with stored data."""


class DocumentVersionConflict(ValueError):
    """Raised when version metadata conflicts with immutable content."""


class DocumentIngestionConflict(ValueError):
    """Raised when retrieval provenance cannot form a document version."""


class DerivedArtifactConflict(ValueError):
    """Raised when an immutable derived artifact conflicts with stored data."""


class ArticleTextProjectionConflict(ValueError):
    """Raised when derived text cannot safely populate a legacy article."""
