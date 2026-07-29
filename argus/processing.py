from enum import Enum


class ProcessingStatus(str, Enum):
    """Lifecycle status of one processing operation."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ProcessingStage(str, Enum):
    """Versioned processing stage applied to a document."""

    PARSING = "parsing"
    DISCOURSE = "discourse"


PARSING_METHOD_VERSION = "trafilatura-v0.1"
DISCOURSE_METHOD_VERSION = "lexical-en-v0.1"