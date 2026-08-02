from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from argus.analysis.calibration import CalibrationSplit, load_calibration_corpus
from argus.analysis.corpus_builder import (
    SOURCE_RECORD_SCHEMA,
    _assign_split,
    build_corpus_from_manifest,
    verify_corpus_build,
    write_corpus_build,
)


SALT = "argus-fixture-v1"


def _text(source_id: str, *, replacement: str | None = None) -> str:
    sentences = []
    for sentence in range(12):
        words = [
            f"{source_id}word{sentence}_{index}" for index in range(24)
        ]
        sentences.append(" ".join(words) + ".")
    value = " ".join(sentences)
    if replacement is not None:
        value = value.replace(f"{source_id}word0_0", replacement, 1)
    return value


def _group_for(split: CalibrationSplit, label: str) -> str:
    for index in range(10_000):
        candidate = f"{label}-{split.value}-{index}"
        assigned = _assign_split(
            candidate,
            split_salt=SALT,
            train_ratio=0.6,
            calibration_ratio=0.2,
        )
        if assigned is split:
            return candidate
    raise AssertionError("Unable to find fixture group.")


class SyntheticOriginCorpusBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.manifest = self.root / "manifest.jsonl"
        self.records: list[dict[str, object]] = []
        for split in CalibrationSplit:
            for label in ("human", "synthetic"):
                source_id = f"{split.value}-{label}"
                self._add_record(
                    source_id,
                    label=label,
                    group=_group_for(split, label),
                    text=_text(source_id),
                )
        self._write_manifest()

    def _add_record(
            self,
            source_id: str,
            *,
            label: str,
            group: str,
            text: str,
            text_path: str | None = None,
    ) -> None:
        relative = text_path or f"{source_id}.txt"
        path = self.sources / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = text.encode("utf-8")
        path.write_bytes(raw)
        if label == "human":
            provenance: dict[str, object] = {
                "kind": "human_source",
                "reference": f"https://example.test/{source_id}",
                "retrieved_at": "2026-08-02T10:00:00Z",
                "acquisition_method": "publisher-export",
            }
        else:
            provenance = {
                "kind": "generator",
                "reference": f"generation-log:{source_id}",
                "generated_at": "2026-08-02T10:00:00Z",
                "provider": "fixture-provider",
                "model": "fixture-model",
                "model_version": "fixture-v1",
                "prompt_sha256": "a" * 64,
                "generation_parameters": {"temperature": 0},
            }
        self.records.append({
            "schema": SOURCE_RECORD_SCHEMA,
            "source_id": source_id,
            "label": label,
            "language": "en",
            "genre": "news",
            "source_group_id": group,
            "text_path": relative,
            "content_sha256": sha256(raw).hexdigest(),
            "provenance": provenance,
        })

    def _write_manifest(self) -> None:
        self.manifest.write_text(
            "\n".join(json.dumps(item) for item in self.records) + "\n",
            encoding="utf-8",
        )

    def test_build_is_deterministic_and_emits_valid_corpus(self) -> None:
        first = build_corpus_from_manifest(
            self.manifest, source_root=self.sources, split_salt=SALT
        )
        self.records.reverse()
        self._write_manifest()
        second = build_corpus_from_manifest(
            self.manifest, source_root=self.sources, split_salt=SALT
        )
        self.assertEqual(first.samples, second.samples)
        self.assertEqual(first.receipt, second.receipt)

        output = self.root / "corpus.jsonl"
        receipt_path = self.root / "receipt.json"
        receipt = write_corpus_build(
            second, output_jsonl=output, receipt_json=receipt_path
        )
        corpus = load_calibration_corpus(output)

        self.assertEqual(receipt["corpus_hash"], corpus.corpus_hash)
        self.assertEqual(receipt["source_records"], 6)
        self.assertEqual(receipt["splits"], {
            "calibration": 2, "test": 2, "train": 2,
        })
        self.assertRegex(receipt["receipt_hash"], r"^[0-9a-f]{64}$")
        self.assertTrue(receipt_path.is_file())
        for sample in corpus.samples:
            self.assertIn("original_sha256", sample.provenance)
            self.assertIn("normalization_policy", sample.provenance)

    def test_changed_source_bytes_are_rejected(self) -> None:
        path = self.sources / "train-human.txt"
        path.write_text(path.read_text(encoding="utf-8") + " changed", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "content SHA-256"):
            build_corpus_from_manifest(
                self.manifest, source_root=self.sources, split_salt=SALT
            )

    def test_path_escape_is_rejected(self) -> None:
        record = self.records[0]
        record["text_path"] = "../outside.txt"
        (self.root / "outside.txt").write_text("outside", encoding="utf-8")
        record["content_sha256"] = sha256(b"outside").hexdigest()
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "escapes source-root"):
            build_corpus_from_manifest(
                self.manifest, source_root=self.sources, split_salt=SALT
            )

    def test_missing_synthetic_generation_provenance_is_rejected(self) -> None:
        record = next(item for item in self.records if item["label"] == "synthetic")
        del record["provenance"]["model_version"]
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "missing: model_version"):
            build_corpus_from_manifest(
                self.manifest, source_root=self.sources, split_salt=SALT
            )

    def test_canonical_duplicate_is_rejected(self) -> None:
        first = self.records[0]
        second = self.records[1]
        original = (self.sources / str(first["text_path"])).read_text(encoding="utf-8")
        duplicate = original.upper().replace(" ", "  ")
        path = self.sources / str(second["text_path"])
        path.write_text(duplicate, encoding="utf-8")
        second["content_sha256"] = sha256(duplicate.encode("utf-8")).hexdigest()
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "duplicate text"):
            build_corpus_from_manifest(
                self.manifest, source_root=self.sources, split_salt=SALT
            )

    def test_near_duplicate_is_rejected(self) -> None:
        first = self.records[0]
        second = self.records[1]
        original = (self.sources / str(first["text_path"])).read_text(encoding="utf-8")
        near = original.replace(f"{first['source_id']}word0_0", "onechangedtoken", 1)
        path = self.sources / str(second["text_path"])
        path.write_text(near, encoding="utf-8")
        second["content_sha256"] = sha256(near.encode("utf-8")).hexdigest()
        self._write_manifest()

        with self.assertRaisesRegex(ValueError, "near-duplicate text"):
            build_corpus_from_manifest(
                self.manifest, source_root=self.sources, split_salt=SALT
            )

    def test_existing_output_is_never_overwritten(self) -> None:
        build = build_corpus_from_manifest(
            self.manifest, source_root=self.sources, split_salt=SALT
        )
        output = self.root / "corpus.jsonl"
        output.write_text("keep", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            write_corpus_build(
                build,
                output_jsonl=output,
                receipt_json=self.root / "receipt.json",
            )
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_build_verifier_reconstructs_every_artifact(self) -> None:
        build = build_corpus_from_manifest(
            self.manifest, source_root=self.sources, split_salt=SALT
        )
        output = self.root / "corpus.jsonl"
        receipt_path = self.root / "receipt.json"
        written = write_corpus_build(
            build, output_jsonl=output, receipt_json=receipt_path
        )

        verified = verify_corpus_build(
            self.manifest,
            source_root=self.sources,
            corpus_path=output,
            receipt_path=receipt_path,
        )

        self.assertEqual(verified, written)

    def test_build_verifier_rejects_tampered_receipt(self) -> None:
        build = build_corpus_from_manifest(
            self.manifest, source_root=self.sources, split_salt=SALT
        )
        output = self.root / "corpus.jsonl"
        receipt_path = self.root / "receipt.json"
        write_corpus_build(
            build, output_jsonl=output, receipt_json=receipt_path
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["source_records"] = 999
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "hash verification"):
            verify_corpus_build(
                self.manifest,
                source_root=self.sources,
                corpus_path=output,
                receipt_path=receipt_path,
            )


if __name__ == "__main__":
    unittest.main()
