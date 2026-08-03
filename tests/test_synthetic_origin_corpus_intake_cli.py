from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from typer.testing import CliRunner

from argus.interface.cli import app


runner = CliRunner()


class SyntheticOriginCorpusIntakeCLITests(unittest.TestCase):
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_register_human_command_creates_source_and_record(
            self, upgrade, configure) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("Affirmatively human source text.", encoding="utf-8")
            workspace = root / "intake"
            result = runner.invoke(app, [
                "register-human-corpus-source",
                "--input-text", str(source),
                "--workspace-root", str(workspace),
                "--source-id", "human-news-0001",
                "--language", "en",
                "--genre", "news",
                "--source-group-id", "story-1",
                "--reference", "https://example.test/story-1",
                "--title", "Example story",
                "--author", "Jane Reporter",
                "--publisher", "Example Publisher",
                "--published-date", "2013-05-14",
                "--text-scope", "article-body",
                "--retrieved-at", "2026-08-02T10:00:00Z",
                "--acquisition-method", "publisher-export",
            ])

            self.assertEqual(result.exit_code, 0, result.stdout)
            self.assertTrue((workspace / "text/human/human-news-0001.txt").is_file())
            self.assertTrue((workspace / "records/human-news-0001.json").is_file())
        self.assertIn("label=human", result.stdout)
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_register_human_command_reports_invalid_timestamp_without_traceback(
            self, upgrade, configure) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("Affirmatively human source text.", encoding="utf-8")
            result = runner.invoke(app, [
                "register-human-corpus-source",
                "--input-text", str(source),
                "--workspace-root", str(root / "intake"),
                "--source-id", "human-news-0001",
                "--language", "en",
                "--genre", "news",
                "--source-group-id", "story-1",
                "--reference", "https://example.test/story-1",
                "--title", "Example story",
                "--author", "Jane Reporter",
                "--publisher", "Example Publisher",
                "--published-date", "2013-05-14",
                "--text-scope", "article-body",
                "--retrieved-at", "not-a-timestamp",
                "--acquisition-method", "manual-preservation",
            ])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("retrieved_at must be RFC 3339", result.output)
        self.assertNotIn("Traceback", result.output)
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_register_synthetic_command_rejects_non_object_parameters(
            self, upgrade, configure) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            prompt = root / "prompt.txt"
            source.write_text("Generated text.", encoding="utf-8")
            prompt.write_text("Prompt.", encoding="utf-8")
            result = runner.invoke(app, [
                "register-synthetic-corpus-source",
                "--input-text", str(source),
                "--prompt-file", str(prompt),
                "--workspace-root", str(root / "intake"),
                "--source-id", "synthetic-news-0001",
                "--language", "en",
                "--genre", "news",
                "--source-group-id", "prompt-1",
                "--generated-at", "2026-08-02T10:00:00Z",
                "--provider", "provider",
                "--model", "model",
                "--model-version", "snapshot",
                "--generation-parameters-json", "[]",
            ])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("must contain one JSON object", result.output)
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_inspect_command_reports_split_and_readiness(
            self, upgrade, configure) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text(
                " ".join(
                    " ".join(f"word{sentence}x{word}" for word in range(25)) + "."
                    for sentence in range(12)
                ),
                encoding="utf-8",
            )
            workspace = root / "intake"
            registered = runner.invoke(app, [
                "register-human-corpus-source",
                "--input-text", str(source),
                "--workspace-root", str(workspace),
                "--source-id", "human-national-geographic",
                "--language", "en",
                "--genre", "science-news",
                "--source-group-id",
                "national-geographic-130514-dogs-domestication",
                "--reference", "https://example.test/story",
                "--title", "Story", "--author", "Reporter",
                "--publisher", "Publisher", "--published-date", "2013-05-14",
                "--text-scope", "article-body",
                "--retrieved-at", "2026-08-02T10:00:00Z",
                "--acquisition-method", "publisher-export",
            ])
            self.assertEqual(registered.exit_code, 0, registered.output)

            result = runner.invoke(app, [
                "inspect-synthetic-corpus-intake",
                "--workspace-root", str(workspace),
                "--split-salt", "synthetic-origin-en-v1",
            ])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("records=1 groups=1 ready_for_build=false", result.output)
        self.assertIn("split=train", result.output)
        self.assertIn("eligible=true", result.output)
        self.assertIn("missing=train:synthetic", result.output)
        self.assertNotIn("Traceback", result.output)
        self.assertEqual(upgrade.call_count, 2)
        self.assertEqual(configure.call_count, 2)


if __name__ == "__main__":
    unittest.main()
