from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.models import RawArtifact
from argus.storage.base_repository import BaseRepository


class RawArtifactMetadataConflict(ValueError):
    """Raised when one artifact identity has conflicting metadata."""


class RawArtifactRepository(BaseRepository[RawArtifact]):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=RawArtifact,
        )

    def get_by_digest(
        self,
        *,
        hash_algorithm: str,
        content_hash: str,
    ) -> RawArtifact | None:
        statement = (
            select(RawArtifact)
            .where(
                RawArtifact.hash_algorithm
                == hash_algorithm,
                RawArtifact.content_hash
                == content_hash,
                )
        )

        return self.session.scalar(statement)

    def get_by_storage_location(
        self,
        *,
        storage_backend: str,
        storage_key: str,
    ) -> RawArtifact | None:
        statement = (
            select(RawArtifact)
            .where(
                RawArtifact.storage_backend
                == storage_backend,
                RawArtifact.storage_key
                == storage_key,
                )
        )

        return self.session.scalar(statement)

    def get_or_create(
        self,
        stored: StoredArtifact,
    ) -> RawArtifact:
        existing_artifact = self.get_by_digest(
            hash_algorithm=stored.hash_algorithm,
            content_hash=stored.content_hash,
        )

        if existing_artifact is not None:
            self._validate_existing(
                artifact=existing_artifact,
                stored=stored,
            )
            return existing_artifact

        artifact_at_location = (
            self.get_by_storage_location(
                storage_backend=stored.storage_backend,
                storage_key=stored.storage_key,
            )
        )

        if artifact_at_location is not None:
            raise RawArtifactMetadataConflict(
                "Storage location is already registered with "
                "another content digest."
            )

        artifact = RawArtifact(
            hash_algorithm=stored.hash_algorithm,
            content_hash=stored.content_hash,
            byte_size=stored.byte_size,
            storage_backend=stored.storage_backend,
            storage_key=stored.storage_key,
        )

        self.add(artifact)
        self.flush()

        return artifact

    @staticmethod
    def _validate_existing(
        *,
        artifact: RawArtifact,
        stored: StoredArtifact,
    ) -> None:
        conflicts: list[str] = []

        if artifact.byte_size != stored.byte_size:
            conflicts.append("byte_size")

        if artifact.storage_backend != stored.storage_backend:
            conflicts.append("storage_backend")

        if artifact.storage_key != stored.storage_key:
            conflicts.append("storage_key")

        if conflicts:
            conflicting_fields = ", ".join(conflicts)
            raise RawArtifactMetadataConflict(
                "Raw artifact metadata conflicts on: "
                f"{conflicting_fields}."
            )
