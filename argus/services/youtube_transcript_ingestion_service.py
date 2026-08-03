from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.acquisition import RawArtifactStore
from argus.config import RAW_ARTIFACT_DIRECTORY
from argus.database import SessionLocal
from argus.models import Document, DocumentVersion
from argus.services.transcript_ingestion_service import (
    TranscriptIngestionResult,
    TranscriptIngestionService,
)
from argus.storage.artifact_store import FileSystemRawArtifactStore
from argus.transcript_sources.youtube import (
    YouTubeTranscriptSource,
    youtube_video_id,
)


_CROSS_LOCATION_LIMITATION = (
    "The transcript was acquired from an external YouTube publication whose "
    "equivalence to the document URI is operator-asserted, not independently "
    "verified by Argus."
)


@dataclass(frozen=True, slots=True)
class YouTubeTranscriptIngestionResult:
    ingestion: TranscriptIngestionResult
    requested_location: str
    resolved_location: str
    video_id: str
    track_id: str
    title: str | None
    cross_location: bool


def ingest_youtube_transcript(
        *,
        document_version_id: int,
        youtube_url: str,
        track_id: str,
        allow_auto_generated: bool = False,
        allow_cross_location: bool = False,
        source: YouTubeTranscriptSource | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        artifact_store: RawArtifactStore | None = None,
) -> YouTubeTranscriptIngestionResult:
    """Retrieve one exact YouTube track and commit its provenance chain."""

    external_video_id = youtube_video_id(youtube_url)
    with session_factory() as session:
        cross_location = _validate_document_relation(
            session,
            document_version_id=document_version_id,
            external_video_id=external_video_id,
            allow_cross_location=allow_cross_location,
        )

    transcript_source = source or YouTubeTranscriptSource()
    retrieved = transcript_source.retrieve(
        youtube_url,
        track_id=track_id,
        allow_auto_generated=allow_auto_generated,
    )
    if retrieved.catalog.video_id != external_video_id:
        raise ValueError(
            "Retrieved YouTube transcript belongs to another video."
        )
    store = artifact_store or FileSystemRawArtifactStore(
        RAW_ARTIFACT_DIRECTORY
    )
    with session_factory() as session:
        try:
            ingestion = TranscriptIngestionService(
                session,
                artifact_store=store,
            ).ingest(
                document_version_id=document_version_id,
                content=retrieved.content,
                provider=retrieved.catalog.provider,
                provider_version=retrieved.catalog.provider_version,
                requested_location=retrieved.catalog.requested_location,
                resolved_location=retrieved.resolved_location,
                external_identifier=(
                    f"youtube:{retrieved.catalog.video_id}:caption:"
                    f"{retrieved.track.track_id}"
                ),
                retrieved_at=retrieved.retrieved_at,
                language=retrieved.track.track_id,
                transcript_kind=retrieved.track.transcript_kind,
                transcript_format=retrieved.track.transcript_format,
                media_type=retrieved.track.media_type,
                additional_quality_limitations=(
                    (_CROSS_LOCATION_LIMITATION,) if cross_location else ()
                ),
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
    return YouTubeTranscriptIngestionResult(
        ingestion=ingestion,
        requested_location=retrieved.catalog.requested_location,
        resolved_location=retrieved.resolved_location,
        video_id=retrieved.catalog.video_id,
        track_id=retrieved.track.track_id,
        title=retrieved.catalog.title,
        cross_location=cross_location,
    )


def _validate_document_relation(
        session: Session,
        *,
        document_version_id: int,
        external_video_id: str,
        allow_cross_location: bool,
) -> bool:
    version = session.get(DocumentVersion, document_version_id)
    if version is None:
        raise ValueError(
            f"Document version does not exist: {document_version_id}."
        )
    document = session.get(Document, version.document_id)
    if document is None:
        raise ValueError(
            "Document version references a missing document."
        )
    document_video_id = None
    if document.identifier_scheme == "uri":
        try:
            document_video_id = youtube_video_id(document.identifier_value)
        except ValueError:
            pass
    cross_location = document_video_id != external_video_id
    if cross_location and not allow_cross_location:
        raise ValueError(
            "The document URI is not the same YouTube video. Use explicit "
            "cross-location authorization only when the external publication "
            "is known to contain the same media."
        )
    return cross_location
