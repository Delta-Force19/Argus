import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from argus.analysis.calibration import (
    SAMPLE_SCHEMA,
    calibrate_threshold,
    corpus_summary,
    evaluate_test_split,
    load_calibration_corpus,
)


def _long_text(*, synthetic: bool, marker: str) -> str:
    if synthetic:
        sentence = (
            "Moreover, it is important to note that the evolving system "
            "plays a crucial role in this multifaceted analytical process"
        )
        return " ".join(f"{sentence} {marker}{index}." for index in range(15))
    sentences = []
    for index in range(25):
        extra = " ".join(f"detail{index}_{part}" for part in range(index % 7))
        sentences.append(
            f"Reporter {marker}{index} checked records, called witnesses, "
            f"and described what remained uncertain {extra}."
        )
    return " ".join(sentences)


def _sample(
        sample_id: str,
        *,
        label: str,
        split: str,
        genre: str = "news",
        group: str | None = None,
) -> dict[str, object]:
    return {
        "schema": SAMPLE_SCHEMA,
        "sample_id": sample_id,
        "label": label,
        "split": split,
        "language": "en",
        "genre": genre,
        "source_group_id": group or sample_id,
        "text": _long_text(
            synthetic=label == "synthetic", marker=sample_id
        ),
        "provenance": {
            "kind": "generator" if label == "synthetic" else "human_source",
            "reference": f"fixture:{sample_id}",
        },
    }


class SyntheticOriginCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "corpus.jsonl"
        self.samples = [
            _sample("train-human", label="human", split="train"),
            _sample("train-ai", label="synthetic", split="train"),
            _sample("cal-human", label="human", split="calibration"),
            _sample("cal-ai", label="synthetic", split="calibration"),
            _sample("test-human", label="human", split="test"),
            _sample("test-ai", label="synthetic", split="test"),
        ]
        self._write(self.samples)

    def _write(self, samples: list[dict[str, object]]) -> None:
        self.path.write_text(
            "\n".join(json.dumps(sample) for sample in samples) + "\n",
            encoding="utf-8",
        )

    def test_corpus_hash_is_stable_across_jsonl_order(self) -> None:
        first = load_calibration_corpus(self.path)
        self._write(list(reversed(self.samples)))
        second = load_calibration_corpus(self.path)

        self.assertEqual(first.corpus_hash, second.corpus_hash)
        self.assertEqual(first.split_hashes, second.split_hashes)
        summary = corpus_summary(first)
        self.assertEqual(summary["samples"], 6)
        self.assertEqual(summary["eligible_samples"], 6)

    def test_corpus_rejects_source_group_leakage_between_splits(self) -> None:
        self.samples[0]["source_group_id"] = "shared"
        self.samples[2]["source_group_id"] = "shared"
        self._write(self.samples)

        with self.assertRaisesRegex(ValueError, "cannot cross"):
            load_calibration_corpus(self.path)

    def test_corpus_rejects_duplicate_text(self) -> None:
        self.samples[1]["text"] = self.samples[0]["text"]
        self._write(self.samples)

        with self.assertRaisesRegex(ValueError, "duplicate text"):
            load_calibration_corpus(self.path)

    def test_corpus_rejects_label_provenance_mismatch(self) -> None:
        self.samples[0]["provenance"] = {
            "kind": "generator",
            "reference": "fixture:mislabeled",
        }
        self._write(self.samples)

        with self.assertRaisesRegex(ValueError, "provenance kind"):
            load_calibration_corpus(self.path)

    def test_threshold_uses_calibration_and_report_uses_test(self) -> None:
        corpus = load_calibration_corpus(self.path)

        decision = calibrate_threshold(
            corpus, software_version="git:" + "a" * 40
        )
        report = evaluate_test_split(
            corpus, decision, software_version="git:" + "a" * 40
        )

        self.assertEqual(decision["schema"], "synthetic-origin-threshold@1")
        self.assertEqual(
            decision["calibration_split_hash"],
            corpus.split_hashes["calibration"],
        )
        self.assertEqual(report["schema"], "synthetic-origin-evaluation@1")
        self.assertEqual(report["test_split_hash"], corpus.split_hashes["test"])
        self.assertFalse(report["score_is_probability"])
        self.assertEqual(report["overall"]["samples"], 2)
        self.assertFalse(report["overall"]["sufficient_sample_size"])
        self.assertIn("news", report["slices"]["genre"])

    def test_evaluation_rejects_tampered_threshold(self) -> None:
        corpus = load_calibration_corpus(self.path)
        decision = calibrate_threshold(
            corpus, software_version="git:" + "a" * 40
        )
        decision["threshold"] = 0.99

        with self.assertRaisesRegex(ValueError, "hash verification"):
            evaluate_test_split(
                corpus, decision, software_version="git:" + "a" * 40
            )

    def test_evaluation_rejects_different_software_version(self) -> None:
        corpus = load_calibration_corpus(self.path)
        decision = calibrate_threshold(
            corpus, software_version="git:" + "a" * 40
        )

        with self.assertRaisesRegex(ValueError, "software version"):
            evaluate_test_split(
                corpus, decision, software_version="git:" + "b" * 40
            )


if __name__ == "__main__":
    unittest.main()
