from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from typer.testing import CliRunner

from argus.interface.cli import app


runner = CliRunner()


class SyntheticOriginCorpusBuilderCLITests(unittest.TestCase):
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    @patch("argus.interface.cli.write_corpus_build")
    @patch("argus.interface.cli.build_corpus_from_manifest")
    def test_build_prints_hash_bound_summary(
            self, build_corpus, write_build, upgrade, configure) -> None:
        write_build.return_value = {
            "schema": "synthetic-origin-corpus-build@1",
            "builder_version": "source-manifest-v0.1",
            "source_records": 90,
            "source_groups": 80,
            "splits": {"train": 54, "calibration": 18, "test": 18},
            "corpus_hash": "a" * 64,
            "receipt_hash": "b" * 64,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.jsonl"
            sources = root / "sources"
            manifest.touch()
            sources.mkdir()
            result = runner.invoke(app, [
                "build-synthetic-corpus",
                "--manifest-jsonl", str(manifest),
                "--source-root", str(sources),
                "--output-jsonl", str(root / "corpus.jsonl"),
                "--receipt-json", str(root / "receipt.json"),
                "--split-salt", "argus-en-v1",
            ])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("sources=90 groups=80", result.stdout)
        self.assertIn(f"corpus_hash={'a' * 64}", result.stdout)
        build_corpus.assert_called_once()
        write_build.assert_called_once()
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    @patch("argus.interface.cli.verify_corpus_build")
    def test_verify_prints_reconstructed_identity(
            self, verify_build, upgrade, configure) -> None:
        verify_build.return_value = {
            "schema": "synthetic-origin-corpus-build@1",
            "source_records": 90,
            "source_groups": 80,
            "corpus_hash": "a" * 64,
            "receipt_hash": "b" * 64,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.jsonl"
            sources = root / "sources"
            corpus = root / "corpus.jsonl"
            receipt = root / "receipt.json"
            manifest.touch()
            sources.mkdir()
            corpus.touch()
            receipt.touch()
            result = runner.invoke(app, [
                "verify-synthetic-corpus-build",
                "--manifest-jsonl", str(manifest),
                "--source-root", str(sources),
                "--corpus-jsonl", str(corpus),
                "--receipt-json", str(receipt),
            ])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("verified=true", result.stdout)
        self.assertIn(f"receipt_hash={'b' * 64}", result.stdout)
        verify_build.assert_called_once()
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
