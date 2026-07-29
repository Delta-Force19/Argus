from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import ProcessingState
from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)
from argus.storage.base_repository import BaseRepository


class ProcessingStateRepository(
    BaseRepository[ProcessingState]
):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=ProcessingState,
        )

    def get(
            self,
            article_id: int,
            stage: ProcessingStage,
            method_version: str,
    ) -> ProcessingState | None:
        statement = select(ProcessingState).where(
            ProcessingState.article_id == article_id,
            ProcessingState.stage == stage,
            ProcessingState.method_version == (
                method_version
            ),
            )

        return self.session.scalar(statement)

    def get_or_create(
            self,
            article_id: int,
            stage: ProcessingStage,
            method_version: str,
    ) -> ProcessingState:
        state = self.get(
            article_id=article_id,
            stage=stage,
            method_version=method_version,
        )

        if state is not None:
            return state

        state = ProcessingState(
            article_id=article_id,
            stage=stage,
            method_version=method_version,
            status=ProcessingStatus.PENDING,
        )

        self.add(state)
        self.session.commit()
        self.refresh(state)

        return state

    def mark_running(
            self,
            state: ProcessingState,
    ) -> None:
        state.status = ProcessingStatus.RUNNING
        state.attempts += 1
        state.last_error = None
        state.updated_at = datetime.now(timezone.utc)

        self.session.commit()

    def mark_done(
            self,
            state: ProcessingState,
    ) -> None:
        state.status = ProcessingStatus.DONE
        state.last_error = None
        state.updated_at = datetime.now(timezone.utc)

        self.session.commit()

    def mark_failed(
            self,
            state: ProcessingState,
            error: str,
    ) -> None:
        state.status = ProcessingStatus.FAILED
        state.last_error = error[:4000]
        state.updated_at = datetime.now(timezone.utc)

        self.session.commit()