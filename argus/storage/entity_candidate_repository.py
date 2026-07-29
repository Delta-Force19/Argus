from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import CanonicalizedEntityCandidate
from argus.models import DerivedArtifact, EntityCandidate
from argus.storage.base_repository import BaseRepository


class EntityCandidateRepository(BaseRepository[EntityCandidate]):
    """Project immutable candidate artifacts into queryable rows."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=EntityCandidate)

    def register(
            self,
            *,
            artifact: DerivedArtifact,
            candidates: Sequence[CanonicalizedEntityCandidate],
    ) -> list[EntityCandidate]:
        if artifact.id is None:
            raise ValueError("artifact must be persisted before projection.")
        if artifact.artifact_type is not DerivedArtifactType.ENTITY_CANDIDATES:
            raise ValueError("artifact must contain entity candidates.")

        existing = self.get_for_artifact(artifact.id)
        if existing:
            if self._signatures(existing) != self._candidate_signatures(
                    candidates
            ):
                raise ValueError(
                    "Stored entity candidates conflict with immutable artifact."
                )
            return existing

        rows = [
            EntityCandidate(
                derived_artifact_id=artifact.id,
                entity_mention_id=candidate.entity_mention_id,
                document_version_id=candidate.document_version_id,
                entity_type=candidate.entity_type,
                canonical_text=candidate.canonical_text,
                context_text=candidate.context_text,
                context_start_char=candidate.context_start_char,
                context_end_char=candidate.context_end_char,
            )
            for candidate in candidates
        ]
        self.session.add_all(rows)
        self.flush()
        return rows

    def get_for_artifact(self, artifact_id: int) -> list[EntityCandidate]:
        statement = (
            select(EntityCandidate)
            .where(EntityCandidate.derived_artifact_id == artifact_id)
            .order_by(
                EntityCandidate.entity_mention_id.asc(),
                EntityCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def get_for_document_version(
            self,
            document_version_id: int,
    ) -> list[EntityCandidate]:
        statement = (
            select(EntityCandidate)
            .where(
                EntityCandidate.document_version_id == document_version_id
            )
            .order_by(
                EntityCandidate.entity_mention_id.asc(),
                EntityCandidate.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    @staticmethod
    def _signatures(
            candidates: Sequence[EntityCandidate],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.entity_mention_id,
                item.document_version_id,
                item.entity_type,
                item.canonical_text,
                item.context_text,
                item.context_start_char,
                item.context_end_char,
            )
            for item in candidates
        ]

    @staticmethod
    def _candidate_signatures(
            candidates: Sequence[CanonicalizedEntityCandidate],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.entity_mention_id,
                item.document_version_id,
                item.entity_type,
                item.canonical_text,
                item.context_text,
                item.context_start_char,
                item.context_end_char,
            )
            for item in candidates
        ]
