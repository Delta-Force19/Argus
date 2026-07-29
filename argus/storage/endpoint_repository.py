from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.endpoints import (
    EndpointConfigurationConflict,
    EndpointType,
)
from argus.models import CollectionEndpoint
from argus.storage.base_repository import BaseRepository


class CollectionEndpointRepository(
    BaseRepository[CollectionEndpoint]
):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=CollectionEndpoint,
        )

    def get_by_identifier(
        self,
        identifier: str,
    ) -> CollectionEndpoint | None:
        statement = (
            select(CollectionEndpoint)
            .where(
                CollectionEndpoint.identifier
                == identifier
            )
        )

        return self.session.scalar(statement)

    def get_by_connector_and_url(
        self,
        *,
        connector_id: str,
        url: str,
    ) -> CollectionEndpoint | None:
        statement = (
            select(CollectionEndpoint)
            .where(
                CollectionEndpoint.connector_id
                == connector_id,
                CollectionEndpoint.url == url,
                )
        )

        return self.session.scalar(statement)

    def get_or_create(
        self,
        *,
        identifier: str,
        endpoint_type: EndpointType,
        connector_id: str,
        url: str,
        source_id: int | None = None,
        language: str | None = None,
    ) -> CollectionEndpoint:
        existing_endpoint = self.get_by_identifier(
            identifier
        )

        if existing_endpoint is not None:
            self._validate_existing(
                endpoint=existing_endpoint,
                endpoint_type=endpoint_type,
                connector_id=connector_id,
                url=url,
                source_id=source_id,
                language=language,
            )
            self._enrich_missing_metadata(
                endpoint=existing_endpoint,
                source_id=source_id,
                language=language,
            )
            return existing_endpoint

        endpoint_at_location = (
            self.get_by_connector_and_url(
                connector_id=connector_id,
                url=url,
            )
        )

        if endpoint_at_location is not None:
            raise EndpointConfigurationConflict(
                "Connector and URL are already registered "
                f"as endpoint '{endpoint_at_location.identifier}'."
            )

        endpoint = CollectionEndpoint(
            identifier=identifier,
            source_id=source_id,
            endpoint_type=endpoint_type,
            connector_id=connector_id,
            url=url,
            language=language,
            is_active=True,
        )

        self.add(endpoint)
        self.flush()

        return endpoint

    def _validate_existing(
        self,
        *,
        endpoint: CollectionEndpoint,
        endpoint_type: EndpointType,
        connector_id: str,
        url: str,
        source_id: int | None,
        language: str | None,
    ) -> None:
        conflicts: list[str] = []

        if endpoint.endpoint_type != endpoint_type:
            conflicts.append("endpoint_type")

        if endpoint.connector_id != connector_id:
            conflicts.append("connector_id")

        if endpoint.url != url:
            conflicts.append("url")

        if (
            endpoint.source_id is not None
            and source_id is not None
            and endpoint.source_id != source_id
        ):
            conflicts.append("source_id")

        if (
            endpoint.language is not None
            and language is not None
            and endpoint.language != language
        ):
            conflicts.append("language")

        if conflicts:
            conflicting_fields = ", ".join(conflicts)
            raise EndpointConfigurationConflict(
                f"Endpoint '{endpoint.identifier}' conflicts "
                f"on: {conflicting_fields}."
            )

    def _enrich_missing_metadata(
        self,
        *,
        endpoint: CollectionEndpoint,
        source_id: int | None,
        language: str | None,
    ) -> None:
        metadata_changed = False

        if endpoint.source_id is None and source_id is not None:
            endpoint.source_id = source_id
            metadata_changed = True

        if endpoint.language is None and language is not None:
            endpoint.language = language
            metadata_changed = True

        if metadata_changed:
            self.flush()
