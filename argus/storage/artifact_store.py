import os
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import mkstemp

from argus.acquisition import (
    ArtifactIntegrityError,
    StoredArtifact,
)


class FileSystemRawArtifactStore:
    """Store immutable response bytes by SHA-256 content address."""

    def __init__(self, root_directory: Path) -> None:
        self._root_directory = root_directory

    @property
    def storage_backend(self) -> str:
        return "filesystem"

    def store(self, content: bytes) -> StoredArtifact:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes.")

        content_hash = sha256(content).hexdigest()
        storage_key = self._storage_key(content_hash)
        artifact_path = self._path_for_key(storage_key)

        artifact_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if artifact_path.exists():
            self._verify_content(
                content=artifact_path.read_bytes(),
                expected_hash=content_hash,
            )
        else:
            self._write_atomically(
                artifact_path=artifact_path,
                content=content,
            )

        return StoredArtifact(
            storage_backend=self.storage_backend,
            storage_key=storage_key,
            hash_algorithm="sha256",
            content_hash=content_hash,
            byte_size=len(content),
        )

    def read(self, storage_key: str) -> bytes:
        artifact_path = self._path_for_key(storage_key)
        content = artifact_path.read_bytes()
        expected_hash = self._hash_from_key(storage_key)

        self._verify_content(
            content=content,
            expected_hash=expected_hash,
        )

        return content

    @staticmethod
    def _storage_key(content_hash: str) -> str:
        return (
            f"sha256/{content_hash[:2]}/"
            f"{content_hash[2:]}"
        )

    def _path_for_key(self, storage_key: str) -> Path:
        key_path = PurePosixPath(storage_key)
        parts = key_path.parts

        if (
            key_path.is_absolute()
            or len(parts) != 3
            or parts[0] != "sha256"
            or len(parts[1]) != 2
            or len(parts[2]) != 62
            or not self._is_hex(parts[1] + parts[2])
        ):
            raise ValueError(
                "storage_key is not a valid SHA-256 artifact key."
            )

        return self._root_directory.joinpath(*parts)

    @staticmethod
    def _hash_from_key(storage_key: str) -> str:
        parts = PurePosixPath(storage_key).parts
        return parts[1] + parts[2]

    @staticmethod
    def _is_hex(value: str) -> bool:
        try:
            int(value, 16)
        except ValueError:
            return False

        return True

    @staticmethod
    def _verify_content(
        *,
        content: bytes,
        expected_hash: str,
    ) -> None:
        actual_hash = sha256(content).hexdigest()

        if actual_hash != expected_hash:
            raise ArtifactIntegrityError(
                "Stored artifact does not match its SHA-256 "
                "content address."
            )

    @staticmethod
    def _write_atomically(
        *,
        artifact_path: Path,
        content: bytes,
    ) -> None:
        file_descriptor, temporary_name = mkstemp(
            prefix=".argus-artifact-",
            dir=artifact_path.parent,
        )
        temporary_path = Path(temporary_name)

        try:
            with os.fdopen(file_descriptor, "wb") as artifact_file:
                artifact_file.write(content)
                artifact_file.flush()
                os.fsync(artifact_file.fileno())

            os.replace(
                temporary_path,
                artifact_path,
            )
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
