import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.acquisition import ArtifactIntegrityError
from argus.storage.artifact_store import (
    FileSystemRawArtifactStore,
)


class FileSystemRawArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root_directory = (
            Path(self.temporary_directory.name) / "artifacts"
        )
        self.store = FileSystemRawArtifactStore(
            self.root_directory
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_store_persists_content_by_sha256(self) -> None:
        content = b"immutable source bytes"
        content_hash = sha256(content).hexdigest()

        stored = self.store.store(content)

        self.assertEqual(stored.storage_backend, "filesystem")
        self.assertEqual(stored.hash_algorithm, "sha256")
        self.assertEqual(stored.content_hash, content_hash)
        self.assertEqual(stored.byte_size, len(content))
        self.assertEqual(
            stored.storage_key,
            f"sha256/{content_hash[:2]}/{content_hash[2:]}",
        )
        self.assertEqual(
            self.store.read(stored.storage_key),
            content,
        )

    def test_store_is_idempotent_for_identical_content(
        self,
    ) -> None:
        content = b"same content"

        first = self.store.store(content)
        second = self.store.store(content)

        self.assertEqual(first, second)
        self.assertEqual(
            len(list(self.root_directory.rglob("*"))),
            3,
        )

    def test_read_rejects_unsafe_or_invalid_keys(self) -> None:
        invalid_keys = (
            "../outside",
            "/absolute/path",
            "sha256/not-a-digest",
            "md5/aa/example",
        )

        for storage_key in invalid_keys:
            with (
                self.subTest(storage_key=storage_key),
                self.assertRaises(ValueError),
            ):
                self.store.read(storage_key)

    def test_read_detects_corrupted_content(self) -> None:
        stored = self.store.store(b"original content")
        artifact_path = self.root_directory.joinpath(
            *stored.storage_key.split("/")
        )
        artifact_path.write_bytes(b"corrupted content")

        with self.assertRaises(ArtifactIntegrityError):
            self.store.read(stored.storage_key)


if __name__ == "__main__":
    unittest.main()
