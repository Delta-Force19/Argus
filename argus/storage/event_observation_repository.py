from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.event_observations import ExtractedEventObservation
from argus.models import (
    DerivedArtifact,
    EventFragmentCandidate,
    EventObservationCandidate,
)
from argus.storage.base_repository import BaseRepository


class EventObservationRepository(BaseRepository[EventObservationCandidate]):
    """Project immutable observation artifacts into queryable rows."""

    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=EventObservationCandidate,
        )

    def register(
            self,
            *,
            artifact: DerivedArtifact,
            fragment: EventFragmentCandidate,
            observations: Sequence[ExtractedEventObservation],
    ) -> list[EventObservationCandidate]:
        if artifact.id is None:
            raise ValueError("artifact must be persisted before projection.")
        if artifact.artifact_type is not DerivedArtifactType.EVENT_OBSERVATIONS:
            raise ValueError("artifact must contain event observations.")
        if fragment.id is None:
            raise ValueError("fragment must be persisted before projection.")
        if artifact.document_version_id != fragment.document_version_id:
            raise ValueError("Observation artifact and fragment disagree.")

        existing = self.get_for_artifact_and_fragment(
            artifact.id,
            fragment.id,
        )
        if existing:
            if self._signatures(existing) != self._candidate_signatures(
                    observations,
                    fragment_start=fragment.start_char,
            ):
                raise ValueError(
                    "Stored event observations conflict with immutable "
                    "artifact."
                )
            return existing

        rows = [
            EventObservationCandidate(
                derived_artifact_id=artifact.id,
                event_fragment_candidate_id=fragment.id,
                document_version_id=fragment.document_version_id,
                observation_type=item.observation_type,
                source_label=item.source_label.strip(),
                surface_text=item.surface_text,
                normalized_value=item.normalized_value,
                start_char=fragment.start_char + item.start_char,
                end_char=fragment.start_char + item.end_char,
                rationale=item.rationale.strip(),
            )
            for item in observations
        ]
        self.session.add_all(rows)
        self.flush()
        return rows

    def get_for_artifact_and_fragment(
            self,
            artifact_id: int,
            fragment_id: int,
    ) -> list[EventObservationCandidate]:
        statement = (
            select(EventObservationCandidate)
            .where(
                EventObservationCandidate.derived_artifact_id == artifact_id,
                EventObservationCandidate.event_fragment_candidate_id
                == fragment_id,
            )
            .order_by(
                EventObservationCandidate.start_char.asc(),
                EventObservationCandidate.end_char.asc(),
                EventObservationCandidate.observation_type.asc(),
                EventObservationCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def get_for_document_version(
            self,
            document_version_id: int,
    ) -> list[EventObservationCandidate]:
        statement = (
            select(EventObservationCandidate)
            .where(
                EventObservationCandidate.document_version_id
                == document_version_id
            )
            .order_by(
                EventObservationCandidate.event_fragment_candidate_id.asc(),
                EventObservationCandidate.start_char.asc(),
                EventObservationCandidate.end_char.asc(),
                EventObservationCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def get_for_artifact(
            self,
            artifact_id: int,
    ) -> list[EventObservationCandidate]:
        statement = (
            select(EventObservationCandidate)
            .where(
                EventObservationCandidate.derived_artifact_id == artifact_id
            )
            .order_by(
                EventObservationCandidate.event_fragment_candidate_id.asc(),
                EventObservationCandidate.start_char.asc(),
                EventObservationCandidate.end_char.asc(),
                EventObservationCandidate.observation_type.asc(),
                EventObservationCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    @staticmethod
    def _signatures(
            observations: Sequence[EventObservationCandidate],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.observation_type,
                item.source_label,
                item.surface_text,
                item.normalized_value,
                item.start_char,
                item.end_char,
                item.rationale,
            )
            for item in observations
        ]

    @staticmethod
    def _candidate_signatures(
            observations: Sequence[ExtractedEventObservation],
            *,
            fragment_start: int,
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.observation_type,
                item.source_label.strip(),
                item.surface_text,
                item.normalized_value,
                fragment_start + item.start_char,
                fragment_start + item.end_char,
                item.rationale.strip(),
            )
            for item in observations
        ]
