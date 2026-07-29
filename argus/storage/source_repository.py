from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.models import Source
from argus.sources import SourceType
from argus.storage.base_repository import BaseRepository


class SourceRepository(BaseRepository[Source]):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=Source,
        )

    def get_by_identifier(
            self,
            identifier: str,
    ) -> Source | None:
        statement = (
            select(Source)
            .where(Source.identifier == identifier)
        )

        return self.session.scalar(statement)

    def get_or_create(
            self,
            *,
            identifier: str,
            name: str,
            source_type: SourceType,
            primary_jurisdiction: str | None = None,
            default_language: str | None = None,
    ) -> Source:
        existing_source = self.get_by_identifier(
            identifier
        )

        if existing_source is not None:
            metadata_changed = False

            if (
                    existing_source.primary_jurisdiction is None
                    and primary_jurisdiction is not None
            ):
                existing_source.primary_jurisdiction = (
                    primary_jurisdiction
                )
                metadata_changed = True

            if (
                    existing_source.default_language is None
                    and default_language is not None
            ):
                existing_source.default_language = (
                    default_language
                )
                metadata_changed = True

            if metadata_changed:
                self.flush()

            return existing_source

        source = Source(
            identifier=identifier,
            name=name,
            source_type=source_type,
            primary_jurisdiction=primary_jurisdiction,
            default_language=default_language,
        )

        self.add(source)
        self.flush()

        return source