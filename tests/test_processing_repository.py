import unittest
from unittest.mock import Mock

from sqlalchemy.orm import Session

from argus.models import ProcessingState
from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)
from argus.storage.processing_repository import (
    ProcessingStateRepository,
)


class ProcessingStateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock(spec=Session)
        self.repository = ProcessingStateRepository(
            self.session
        )

    def create_state(self) -> ProcessingState:
        return ProcessingState(
            article_id=1,
            stage=ProcessingStage.PARSING,
            method_version="test-v1",
            status=ProcessingStatus.PENDING,
            attempts=0,
        )

    def test_get_or_create_uses_pending_status(
            self,
    ) -> None:
        self.session.scalar.return_value = None

        state = self.repository.get_or_create(
            article_id=1,
            stage=ProcessingStage.PARSING,
            method_version="test-v1",
        )

        self.assertEqual(
            state.status,
            ProcessingStatus.PENDING,
        )
        self.session.add.assert_called_once_with(state)
        self.session.commit.assert_called_once_with()
        self.session.refresh.assert_called_once_with(state)

    def test_mark_running_updates_state(self) -> None:
        state = self.create_state()
        state.last_error = "previous failure"

        self.repository.mark_running(state)

        self.assertEqual(
            state.status,
            ProcessingStatus.RUNNING,
        )
        self.assertEqual(state.attempts, 1)
        self.assertIsNone(state.last_error)
        self.session.commit.assert_called_once_with()

    def test_mark_done_updates_state(self) -> None:
        state = self.create_state()
        state.last_error = "previous failure"

        self.repository.mark_done(state)

        self.assertEqual(
            state.status,
            ProcessingStatus.DONE,
        )
        self.assertIsNone(state.last_error)
        self.session.commit.assert_called_once_with()

    def test_mark_failed_records_error(self) -> None:
        state = self.create_state()

        self.repository.mark_failed(
            state,
            error="parser failure",
        )

        self.assertEqual(
            state.status,
            ProcessingStatus.FAILED,
        )
        self.assertEqual(
            state.last_error,
            "parser failure",
        )
        self.session.commit.assert_called_once_with()

    def test_mark_failed_limits_error_length(self) -> None:
        state = self.create_state()
        error = "x" * 5000

        self.repository.mark_failed(
            state,
            error=error,
        )

        self.assertEqual(
            len(state.last_error),
            4000,
        )


if __name__ == "__main__":
    unittest.main()