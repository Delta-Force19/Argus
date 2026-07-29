from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from argus.acquisition import (
    CandidateRecord,
    Connector,
    RawArtifactStore,
    RetrievalOutcome,
)
from argus.models import (
    AcquisitionCandidate,
    CollectionEndpoint,
    RetrievalAttempt,
)
from argus.storage.raw_artifact_repository import (
    RawArtifactRepository,
)
from argus.storage.retrieval_repository import (
    RetrievalAttemptRepository,
    RetrievalProvenanceConflict,
)


class RetrievalService:
    """Retrieve one persisted candidate and record its provenance.

    Database transaction boundaries remain with the caller. Artifact bytes
    are content-addressed and immutable, so a later database rollback may
    leave an unreferenced but safely reusable file in the artifact store.
    """

    def __init__(
        self,
        *,
        session: Session,
        artifact_store: RawArtifactStore,
    ) -> None:
        self._artifact_store = artifact_store
        self._artifact_repository = RawArtifactRepository(
            session
        )
        self._attempt_repository = RetrievalAttemptRepository(
            session
        )

    def retrieve_candidate(
        self,
        *,
        endpoint: CollectionEndpoint,
        candidate: AcquisitionCandidate,
        connector: Connector,
        request_metadata: Mapping[str, object] | None = None,
    ) -> RetrievalAttempt:
        """Retrieve and persist one exact discovery snapshot."""

        self._validate_inputs(
            endpoint=endpoint,
            candidate=candidate,
            connector=connector,
        )
        candidate_record = self._to_candidate_record(candidate)
        result = connector.retrieve(candidate_record)

        if result.candidate.fingerprint != candidate.fingerprint:
            raise RetrievalProvenanceConflict(
                "Connector result does not match the persisted "
                "acquisition candidate fingerprint."
            )

        raw_artifact = None

        if result.outcome is RetrievalOutcome.SUCCEEDED:
            content = result.content

            if content is None:
                raise RetrievalProvenanceConflict(
                    "Successful retrieval did not contain bytes."
                )

            stored_artifact = self._artifact_store.store(content)
            raw_artifact = self._artifact_repository.get_or_create(
                stored_artifact
            )

        return self._attempt_repository.record_result(
            endpoint=endpoint,
            result=result,
            article_id=candidate.article_id,
            stored_candidate=candidate,
            raw_artifact=raw_artifact,
            request_metadata=request_metadata,
        )

    @staticmethod
    def _validate_inputs(
        *,
        endpoint: CollectionEndpoint,
        candidate: AcquisitionCandidate,
        connector: Connector,
    ) -> None:
        if endpoint.id is None:
            raise ValueError(
                "endpoint must be persisted before retrieval."
            )

        if candidate.id is None:
            raise ValueError(
                "candidate must be persisted before retrieval."
            )

        if candidate.endpoint_id != endpoint.id:
            raise RetrievalProvenanceConflict(
                "Acquisition candidate belongs to another endpoint."
            )

        if connector.connector_id != endpoint.connector_id:
            raise RetrievalProvenanceConflict(
                "Connector does not match the collection endpoint."
            )

        if candidate.connector_id != connector.connector_id:
            raise RetrievalProvenanceConflict(
                "Acquisition candidate does not match the connector."
            )

        if candidate.connector_version != connector.connector_version:
            raise RetrievalProvenanceConflict(
                "Acquisition candidate connector version does not "
                "match the retrieval connector version."
            )

    @classmethod
    def _to_candidate_record(
        cls,
        candidate: AcquisitionCandidate,
    ) -> CandidateRecord:
        record = CandidateRecord(
            connector_id=candidate.connector_id,
            connector_version=candidate.connector_version,
            location=candidate.location,
            discovered_at=cls._as_utc(
                candidate.last_discovered_at
            ),
            external_identifier=candidate.external_identifier,
            title=candidate.title,
            source_identifier=candidate.source_identifier,
            media_type=candidate.media_type,
            language=candidate.language,
            published_at=(
                cls._as_utc(candidate.published_at)
                if candidate.published_at is not None
                else None
            ),
        )

        if record.fingerprint != candidate.fingerprint:
            raise RetrievalProvenanceConflict(
                "Persisted acquisition candidate metadata does not "
                "match its fingerprint."
            )

        return record

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)
