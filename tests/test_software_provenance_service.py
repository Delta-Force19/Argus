import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.services.software_provenance_service import (
    resolve_software_provenance,
)


class SoftwareProvenanceServiceTests(unittest.TestCase):
    def test_clean_git_worktree_uses_full_head_revision(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            revision = self._initialize_repository(root)

            result = resolve_software_provenance(root)

        self.assertEqual(result.kind, "git")
        self.assertEqual(result.revision, revision)
        self.assertEqual(result.software_version, f"git:{revision}")

    def test_dirty_tracked_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            self._initialize_repository(root)
            (root / "argus" / "module.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "dirty Argus Git worktree",
            ):
                resolve_software_provenance(root)

    def test_untracked_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            self._initialize_repository(root)
            (root / "argus" / "untracked.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "untracked.py",
            ):
                resolve_software_provenance(root)

    def test_gitignored_runtime_file_does_not_dirty_source(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            with (root / ".gitignore").open(
                "a",
                encoding="utf-8",
            ) as ignore_file:
                ignore_file.write("logs/\n")
            revision = self._initialize_repository(root)
            logs = root / "logs"
            logs.mkdir()
            (logs / "argus.log").write_text(
                "runtime output\n",
                encoding="utf-8",
            )

            result = resolve_software_provenance(root)

        self.assertEqual(result.software_version, f"git:{revision}")

    def test_git_verification_failure_does_not_fall_back(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            (root / ".git").mkdir()

            with patch(
                "argus.services.software_provenance_service."
                "subprocess.run",
                side_effect=FileNotFoundError,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "Git metadata is present",
                ):
                    resolve_software_provenance(root)

    def test_unpacked_source_uses_deterministic_content_hash(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)

            first = resolve_software_provenance(root)
            second = resolve_software_provenance(root)
            (root / "argus" / "module.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )
            changed = resolve_software_provenance(root)

        self.assertEqual(first, second)
        self.assertEqual(first.kind, "source-sha256")
        self.assertEqual(
            first.software_version,
            f"source-sha256:{first.revision}",
        )
        self.assertEqual(len(first.revision), 64)
        self.assertNotEqual(changed, first)

    def test_generated_bytecode_does_not_change_source_hash(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_source(root)
            first = resolve_software_provenance(root)
            cache = root / "argus" / "__pycache__"
            cache.mkdir()
            (cache / "module.cpython-313.pyc").write_bytes(b"generated")

            second = resolve_software_provenance(root)

        self.assertEqual(second, first)

    def test_empty_unpacked_source_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(
                ValueError,
                "no source files were found",
            ):
                resolve_software_provenance(
                    Path(temporary_directory)
                )

    @staticmethod
    def _write_source(root: Path) -> None:
        package = root / "argus"
        package.mkdir()
        (package / "module.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        (root / "main.py").write_text(
            "from argus import module\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(
            "**/__pycache__/\n*.py[cod]\n",
            encoding="utf-8",
        )

    @staticmethod
    def _initialize_repository(root: Path) -> str:
        commands = (
            ("git", "init", "-q"),
            ("git", "config", "user.name", "Argus Tests"),
            ("git", "config", "user.email", "argus@example.invalid"),
            ("git", "add", "."),
            ("git", "commit", "-qm", "Initial source"),
        )
        for command in commands:
            subprocess.run(command, cwd=root, check=True)
        return subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
