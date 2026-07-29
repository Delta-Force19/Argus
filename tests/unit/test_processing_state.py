from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)


def test_processing_status_values():
    assert ProcessingStatus.PENDING.value == "pending"
    assert ProcessingStatus.RUNNING.value == "running"
    assert ProcessingStatus.DONE.value == "done"
    assert ProcessingStatus.FAILED.value == "failed"


def test_processing_stage_values():
    assert ProcessingStage.PARSING.value == "parsing"
    assert ProcessingStage.DISCOURSE.value == "discourse"