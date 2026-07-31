from enum import Enum


class AnalysisRunStatus(str, Enum):
    """Lifecycle state of a persisted analytical execution contract."""

    PREPARED = "prepared"
