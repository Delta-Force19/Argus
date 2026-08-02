from enum import Enum


class AnalysisRunStatus(str, Enum):
    """Lifecycle state of a persisted analytical execution contract."""

    PREPARED = "prepared"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisAttemptStatus(str, Enum):
    """Terminal or active state of one immutable execution attempt."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
