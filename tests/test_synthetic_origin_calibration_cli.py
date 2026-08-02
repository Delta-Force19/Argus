from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from typer.testing import CliRunner

from argus.interface.cli import app


runner = CliRunner()


class SyntheticOriginCalibrationCLITests(unittest.TestCase):
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    @patch("argus.interface.cli.load_calibration_corpus")
    @patch("argus.interface.cli.corpus_summary")
    def test_validate_prints_fingerprinted_split_summary(
            self, corpus_summary, load_corpus, upgrade, configure) -> None:
        corpus_summary.return_value = {
            "schema": "synthetic-origin-calibration-sample@1",
            "corpus_hash": "a" * 64,
            "samples": 60,
            "eligible_samples": 60,
            "labels": {"human": 30, "synthetic": 30},
            "splits": {"calibration": 20, "test": 20, "train": 20},
            "languages": {"en": 60},
            "genres": {"news": 60},
            "split_hashes": {
                "train": "b" * 64,
                "calibration": "c" * 64,
                "test": "d" * 64,
            },
        }
        with TemporaryDirectory() as directory:
            corpus_path = Path(directory) / "corpus.jsonl"
            corpus_path.touch()
            result = runner.invoke(
                app,
                ["validate-synthetic-corpus", "--input-jsonl", str(corpus_path)],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("samples=60 eligible=60", result.stdout)
        self.assertIn(f"corpus_hash={'a' * 64}", result.stdout)
        upgrade.assert_called_once_with()
        configure.assert_called_once_with()

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    @patch("argus.interface.cli.write_canonical_json")
    @patch("argus.interface.cli.resolve_software_provenance")
    @patch("argus.interface.cli.load_calibration_corpus")
    @patch("argus.interface.cli.calibrate_threshold")
    def test_calibrate_writes_hash_bound_threshold(
            self, calibrate, load_corpus, provenance, write_json,
            upgrade, configure) -> None:
        decision = {
            "schema": "synthetic-origin-threshold@1",
            "method": "synthetic-origin-text",
            "method_version": "structural-en-v0.1",
            "software_version": "git:" + "a" * 40,
            "threshold": 0.42,
            "decision_hash": "a" * 64,
        }
        calibrate.return_value = decision
        provenance.return_value.software_version = "git:" + "a" * 40
        with TemporaryDirectory() as directory:
            corpus_path = Path(directory) / "corpus.jsonl"
            output_path = Path(directory) / "threshold.json"
            corpus_path.touch()
            result = runner.invoke(app, [
                "calibrate-synthetic-origin",
                "--input-jsonl", str(corpus_path),
                "--output-json", str(output_path),
            ])

        self.assertEqual(result.exit_code, 0)
        write_json.assert_called_once()
        self.assertEqual(write_json.call_args.args[1], decision)
        self.assertIn("threshold=0.42", result.stdout)

    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    @patch("argus.interface.cli.write_canonical_json")
    @patch("argus.interface.cli.resolve_software_provenance")
    @patch("argus.interface.cli.load_calibration_corpus")
    @patch("argus.interface.cli.evaluate_test_split")
    def test_evaluate_prints_held_out_error_rates(
            self, evaluate, load_corpus, provenance, write_json,
            upgrade, configure) -> None:
        evaluate.return_value = {
            "schema": "synthetic-origin-evaluation@1",
            "method": "synthetic-origin-text",
            "method_version": "structural-en-v0.1",
            "software_version": "git:" + "a" * 40,
            "threshold": 0.42,
            "test_split_hash": "a" * 64,
            "report_hash": "b" * 64,
            "overall": {
                "samples": 60,
                "roc_auc": 0.75,
                "balanced_accuracy": 0.7,
                "false_positive_rate": {"rate": 0.1},
                "false_negative_rate": {"rate": 0.2},
                "sufficient_sample_size": True,
            },
        }
        provenance.return_value.software_version = "git:" + "a" * 40
        with TemporaryDirectory() as directory:
            corpus_path = Path(directory) / "corpus.jsonl"
            threshold_path = Path(directory) / "threshold.json"
            output_path = Path(directory) / "report.json"
            corpus_path.touch()
            threshold_path.write_text("{}", encoding="utf-8")
            result = runner.invoke(app, [
                "evaluate-synthetic-origin",
                "--input-jsonl", str(corpus_path),
                "--threshold-json", str(threshold_path),
                "--output-json", str(output_path),
            ])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("false_positive_rate=0.1", result.stdout)
        self.assertIn("sufficient=true", result.stdout)
        write_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
