from enum import Enum


class AcquisitionStage(str, Enum):
    """Bounded stage in which one acquisition item stopped."""

    PREPARATION = "preparation"
    RETRIEVAL = "retrieval"
    PROCESSING = "processing"
    COMMIT = "commit"


class AcquisitionStageError(RuntimeError):
    """Preserve an acquisition stage without hiding the original error."""

    def __init__(
            self,
            stage: AcquisitionStage,
            original_error: Exception,
    ) -> None:
        super().__init__(str(original_error))
        self.stage = stage
        self.original_error = original_error
