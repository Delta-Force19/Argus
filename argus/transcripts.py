from enum import Enum


class TranscriptFormat(str, Enum):
    """Serialized form of retrieved transcript bytes."""

    PLAIN_TEXT = "plain_text"
    WEBVTT = "webvtt"
    SUBRIP = "subrip"


class TranscriptKind(str, Enum):
    """How the transcript text was produced upstream."""

    PUBLISHER_PROVIDED = "publisher_provided"
    HUMAN_CREATED = "human_created"
    AUTO_GENERATED = "auto_generated"
    UNKNOWN = "unknown"


class TranscriptProvenanceConflict(ValueError):
    """Raised when immutable transcript acquisition metadata conflicts."""
