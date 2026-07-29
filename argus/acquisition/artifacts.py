from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ArtifactIntegrityError(RuntimeError):
    """Raised when stored bytes do not match their recorded digest."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Location and integrity metadata returned by an artifact store."""

    storage_backend: str
    storage_key: str
    hash_algorithm: str
    content_hash: str
    byte_size: int

    def __post_init__(self) -> None:
        required_values = {
            "storage_backend": self.storage_backend,
            "storage_key": self.storage_key,
            "hash_algorithm": self.hash_algorithm,
            "content_hash": self.content_hash,
        }

        for field_name, value in required_values.items():
            if not value.strip():
                raise ValueError(
                    f"{field_name} must not be blank."
                )

        if self.byte_size < 0:
            raise ValueError(
                "byte_size must not be negative."
            )


@runtime_checkable
class RawArtifactStore(Protocol):
    """Store and retrieve unchanged acquisition response bytes."""

    @property
    def storage_backend(self) -> str:
        """Return the stable identifier of this storage implementation."""

        ...

    def store(self, content: bytes) -> StoredArtifact:
        """Persist bytes without changing their content."""

        ...

    def read(self, storage_key: str) -> bytes:
        """Read bytes and verify their recorded content address."""

        ...
