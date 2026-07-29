import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from argus.acquisition import StoredArtifact
from argus.database import Base
from argus.models import RawArtifact
from argus.storage.raw_artifact_repository import (
    RawArtifactMetadataConflict,
    RawArtifactRepository,
)


class RawArtifactRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = RawArtifactRepository(
            self.session
        )
        self.stored = StoredArtifact(
            storage_backend="filesystem",
            storage_key=(
                f"sha256/ab/{'ab' * 31}"
            ),
            hash_algorithm="sha256",
            content_hash="ab" * 32,
            byte_size=128,
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_get_or_create_adds_artifact_without_commit(
        self,
    ) -> None:
        artifact = self.repository.get_or_create(
            self.stored
        )

        self.assertIsNotNone(artifact.id)
        self.assertEqual(artifact.hash_algorithm, "sha256")
        self.assertEqual(
            artifact.content_hash,
            self.stored.content_hash,
        )
        self.assertEqual(artifact.byte_size, 128)
        self.assertEqual(
            artifact.storage_backend,
            "filesystem",
        )

        self.session.rollback()

        self.assertIsNone(
            self.repository.get_by_digest(
                hash_algorithm="sha256",
                content_hash=self.stored.content_hash,
            )
        )

    def test_get_or_create_returns_existing_artifact(
        self,
    ) -> None:
        first = self.repository.get_or_create(
            self.stored
        )
        second = self.repository.get_or_create(
            self.stored
        )
        artifact_count = self.session.scalar(
            select(func.count()).select_from(RawArtifact)
        )

        self.assertIs(first, second)
        self.assertEqual(artifact_count, 1)

    def test_get_or_create_rejects_digest_metadata_conflict(
        self,
    ) -> None:
        self.repository.get_or_create(self.stored)
        conflicting = StoredArtifact(
            storage_backend="filesystem",
            storage_key=self.stored.storage_key,
            hash_algorithm="sha256",
            content_hash=self.stored.content_hash,
            byte_size=129,
        )

        with self.assertRaisesRegex(
            RawArtifactMetadataConflict,
            "byte_size",
        ):
            self.repository.get_or_create(conflicting)

    def test_get_or_create_rejects_storage_location_collision(
        self,
    ) -> None:
        self.repository.get_or_create(self.stored)
        conflicting = StoredArtifact(
            storage_backend="filesystem",
            storage_key=self.stored.storage_key,
            hash_algorithm="sha256",
            content_hash="cd" * 32,
            byte_size=128,
        )

        with self.assertRaisesRegex(
            RawArtifactMetadataConflict,
            "Storage location",
        ):
            self.repository.get_or_create(conflicting)


if __name__ == "__main__":
    unittest.main()
