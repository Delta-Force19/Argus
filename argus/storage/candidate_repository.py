from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.acquisition import CandidateRecord
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
)
from argus.storage.base_repository import BaseRepository


class CandidateProvenanceConflict(ValueError):
    """Raised when candidate provenance conflicts with stored data."""


class AcquisitionCandidateRepository(
    BaseRepository[AcquisitionCandidate]
):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=AcquisitionCandidate,
        )

    def get_by_endpoint_and_fingerprint(
        self,
        *,
        endpoint_id: int,
        fingerprint: str,
    ) -> AcquisitionCandidate | None:
        statement = (
            select(AcquisitionCandidate)
            .where(
                AcquisitionCandidate.endpoint_id
                == endpoint_id,
                AcquisitionCandidate.fingerprint
                == fingerprint,
                )
        )

        return self.session.scalar(statement)

    def get_or_create(
        self,
        *,
        endpoint: CollectionEndpoint,
        candidate: CandidateRecord,
        article_id: int | None = None,
    ) -> AcquisitionCandidate:
        if endpoint.id is None:
            raise ValueError(
                "endpoint must be persisted before candidate is recorded."
            )

        if candidate.connector_id != endpoint.connector_id:
            raise CandidateProvenanceConflict(
                "Candidate connector does not match the collection "
                "endpoint connector."
            )

        existing_candidate = (
            self.get_by_endpoint_and_fingerprint(
                endpoint_id=endpoint.id,
                fingerprint=candidate.fingerprint,
            )
        )

        if existing_candidate is not None:
            self._record_rediscovery(
                stored=existing_candidate,
                candidate=candidate,
                article_id=article_id,
            )
            return existing_candidate

        stored_candidate = AcquisitionCandidate(
            endpoint_id=endpoint.id,
            article_id=article_id,
            fingerprint=candidate.fingerprint,
            connector_id=candidate.connector_id,
            connector_version=candidate.connector_version,
            external_identifier=candidate.external_identifier,
            location=candidate.location,
            title=candidate.title,
            source_identifier=candidate.source_identifier,
            media_type=candidate.media_type,
            language=candidate.language,
            published_at=candidate.published_at,
            first_discovered_at=candidate.discovered_at,
            last_discovered_at=candidate.discovered_at,
            discovery_count=1,
        )

        self.add(stored_candidate)
        self.flush()

        return stored_candidate

    def _record_rediscovery(
        self,
        *,
        stored: AcquisitionCandidate,
        candidate: CandidateRecord,
        article_id: int | None,
    ) -> None:
        if (
            stored.article_id is not None
            and article_id is not None
            and stored.article_id != article_id
        ):
            raise CandidateProvenanceConflict(
                "Candidate is already linked to another article."
            )

        if stored.article_id is None and article_id is not None:
            stored.article_id = article_id

        if self._as_utc(candidate.discovered_at) < self._as_utc(
            stored.first_discovered_at
        ):
            stored.first_discovered_at = candidate.discovered_at

        if self._as_utc(candidate.discovered_at) > self._as_utc(
            stored.last_discovered_at
        ):
            stored.last_discovered_at = candidate.discovered_at

        stored.discovery_count += 1
        self.flush()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)
