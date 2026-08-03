from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import EventFragmentCandidate
from argus.storage.base_repository import BaseRepository


class EventFragmentRepository(BaseRepository[EventFragmentCandidate]):
    """Persist source-anchored fragment candidates without assigning events."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=EventFragmentCandidate)

    def get_origin(
            self,
            *,
            text_derived_artifact_id: int,
            start_char: int,
            end_char: int,
            method: str,
            method_version: str,
    ) -> EventFragmentCandidate | None:
        return self.session.scalar(
            select(EventFragmentCandidate).where(
                EventFragmentCandidate.text_derived_artifact_id
                == text_derived_artifact_id,
                EventFragmentCandidate.start_char == start_char,
                EventFragmentCandidate.end_char == end_char,
                EventFragmentCandidate.method == method,
                EventFragmentCandidate.method_version == method_version,
            )
        )

    def get_for_document_version(
            self,
            document_version_id: int,
    ) -> list[EventFragmentCandidate]:
        statement = (
            select(EventFragmentCandidate)
            .where(
                EventFragmentCandidate.document_version_id
                == document_version_id
            )
            .order_by(
                EventFragmentCandidate.start_char.asc(),
                EventFragmentCandidate.end_char.asc(),
                EventFragmentCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())
