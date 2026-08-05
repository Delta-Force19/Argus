from enum import Enum


def canonicalize_transcript_source(content: bytes) -> str:
    """Decode exact UTF-8 bytes and canonicalize only newline serialization."""

    if not content:
        raise ValueError("Transcript content must not be empty.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Transcript content must be UTF-8 encoded.") from error
    return decoded.replace("\r\n", "\n").replace("\r", "\n")


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
