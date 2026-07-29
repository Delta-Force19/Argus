from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import RecognizedEntityMention
from argus.models import DerivedArtifact, EntityMention
from argus.storage.base_repository import BaseRepository


class EntityMentionRepository(BaseRepository[EntityMention]):
    """Project immutable entity-mention artifacts into queryable rows."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=EntityMention)

    def register(
            self,
            *,
            artifact: DerivedArtifact,
            mentions: Sequence[RecognizedEntityMention],
    ) -> list[EntityMention]:
        if artifact.id is None:
            raise ValueError("artifact must be persisted before projection.")
        if artifact.artifact_type is not DerivedArtifactType.ENTITY_MENTIONS:
            raise ValueError("artifact must contain entity mentions.")

        existing = self.get_for_artifact(artifact.id)
        if existing:
            if self._signatures(existing) != self._candidate_signatures(mentions):
                raise ValueError(
                    "Stored entity mentions conflict with immutable artifact."
                )
            return existing

        rows = [
            EntityMention(
                derived_artifact_id=artifact.id,
                document_version_id=artifact.document_version_id,
                entity_type=mention.entity_type,
                source_label=mention.source_label.strip(),
                surface_text=mention.surface_text,
                normalized_text=mention.normalized_text,
                start_char=mention.start_char,
                end_char=mention.end_char,
            )
            for mention in mentions
        ]
        self.session.add_all(rows)
        self.flush()
        return rows

    def get_for_artifact(self, artifact_id: int) -> list[EntityMention]:
        statement = (
            select(EntityMention)
            .where(EntityMention.derived_artifact_id == artifact_id)
            .order_by(
                EntityMention.start_char.asc(),
                EntityMention.end_char.asc(),
                EntityMention.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    def get_for_document_version(
            self,
            document_version_id: int,
    ) -> list[EntityMention]:
        statement = (
            select(EntityMention)
            .where(
                EntityMention.document_version_id == document_version_id
            )
            .order_by(
                EntityMention.start_char.asc(),
                EntityMention.end_char.asc(),
                EntityMention.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    @staticmethod
    def _signatures(
            mentions: Sequence[EntityMention],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.entity_type,
                item.source_label,
                item.surface_text,
                item.normalized_text,
                item.start_char,
                item.end_char,
            )
            for item in mentions
        ]

    @staticmethod
    def _candidate_signatures(
            mentions: Sequence[RecognizedEntityMention],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.entity_type,
                item.source_label.strip(),
                item.surface_text,
                item.normalized_text,
                item.start_char,
                item.end_char,
            )
            for item in mentions
        ]
