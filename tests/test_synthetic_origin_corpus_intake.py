from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from argus.analysis.corpus_intake import (
    GENERATION_LOG_SCHEMA,
    assemble_source_manifest,
    register_human_source,
    register_synthetic_source,
)


def _long_text(marker: str) -> str:
    return " ".join(
        " ".join(f"{marker}-{sentence}-{word}" for word in range(25)) + "."
        for sentence in range(12)
    )


class SyntheticOriginCorpusIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "intake"

    def _input(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_human_registration_preserves_bytes_and_creates_exact_record(self) -> None:
        original = "\ufeffHuman source\r\nwith exact bytes."
        source = self._input("human.txt", original)

        result = register_human_source(
            source,
            workspace_root=self.workspace,
            source_id="human-news-0001",
            language="EN",
            genre="News",
            source_group_id="publisher-story-1",
            reference="https://example.test/story",
            retrieved_at="2026-08-02T10:00:00Z",
            acquisition_method="publisher-export",
        )

        self.assertEqual(result.text_path.read_bytes(), source.read_bytes())
        record = json.loads(result.record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["label"], "human")
        self.assertEqual(record["language"], "en")
        self.assertEqual(record["text_path"], "human/human-news-0001.txt")
        self.assertEqual(
            record["content_sha256"], sha256(source.read_bytes()).hexdigest()
        )

    def test_synthetic_registration_binds_text_prompt_and_generation_log(self) -> None:
        source = self._input("synthetic.txt", "Generated text.")
        prompt = self._input("prompt.txt", "Write a short report.")

        result = register_synthetic_source(
            source,
            prompt,
            workspace_root=self.workspace,
            source_id="synthetic-news-0001",
            language="en",
            genre="news",
            source_group_id="prompt-family-1",
            generated_at="2026-08-02T10:01:00+00:00",
            provider="provider",
            model="model",
            model_version="snapshot-1",
            generation_parameters={"temperature": 0.2, "seed": 7},
        )

        log = json.loads(result.generation_log_path.read_text(encoding="utf-8"))
        record = json.loads(result.record_path.read_text(encoding="utf-8"))
        self.assertEqual(log["schema"], GENERATION_LOG_SCHEMA)
        self.assertRegex(log["log_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(log["prompt_sha256"], sha256(prompt.read_bytes()).hexdigest())
        self.assertEqual(record["provenance"]["prompt_sha256"], log["prompt_sha256"])
        self.assertEqual(
            record["provenance"]["reference"],
            "generation-log:generation-logs/synthetic-news-0001.json",
        )

    def test_registration_never_overwrites_existing_artifacts(self) -> None:
        source = self._input("human.txt", "Original human text.")
        arguments = dict(
            workspace_root=self.workspace,
            source_id="human-1",
            language="en",
            genre="news",
            source_group_id="group-1",
            reference="https://example.test/1",
            retrieved_at="2026-08-02T10:00:00Z",
            acquisition_method="manual-export",
        )
        first = register_human_source(source, **arguments)
        before = first.text_path.read_bytes()

        with self.assertRaises(FileExistsError):
            register_human_source(source, **arguments)

        self.assertEqual(first.text_path.read_bytes(), before)

    def test_unsafe_source_id_is_rejected_before_any_write(self) -> None:
        source = self._input("human.txt", "Human text.")

        with self.assertRaisesRegex(ValueError, "source_id"):
            register_human_source(
                source,
                workspace_root=self.workspace,
                source_id="../escape",
                language="en",
                genre="news",
                source_group_id="group",
                reference="https://example.test",
                retrieved_at="2026-08-02T10:00:00Z",
                acquisition_method="manual",
            )

        self.assertFalse(self.workspace.exists())

    def test_non_finite_generation_parameter_is_rejected(self) -> None:
        source = self._input("synthetic.txt", "Generated text.")
        prompt = self._input("prompt.txt", "Prompt.")

        with self.assertRaisesRegex(ValueError, "finite JSON"):
            register_synthetic_source(
                source,
                prompt,
                workspace_root=self.workspace,
                source_id="synthetic-1",
                language="en",
                genre="news",
                source_group_id="group",
                generated_at="2026-08-02T10:00:00Z",
                provider="provider",
                model="model",
                model_version="version",
                generation_parameters={"temperature": float("nan")},
            )

    def test_manifest_is_sorted_and_validated_through_builder(self) -> None:
        human = self._input("human.txt", _long_text("human"))
        synthetic = self._input("synthetic.txt", _long_text("synthetic"))
        prompt = self._input("prompt.txt", "Write a report with original wording.")
        register_synthetic_source(
            synthetic, prompt, workspace_root=self.workspace,
            source_id="z-synthetic", language="en", genre="news",
            source_group_id="synthetic-group",
            generated_at="2026-08-02T10:00:00Z", provider="provider",
            model="model", model_version="version",
            generation_parameters={"temperature": 0},
        )
        register_human_source(
            human, workspace_root=self.workspace,
            source_id="a-human", language="en", genre="news",
            source_group_id="human-group", reference="https://example.test/human",
            retrieved_at="2026-08-02T10:00:00Z",
            acquisition_method="publisher-export",
        )
        output = self.root / "manifest.jsonl"

        summary = assemble_source_manifest(
            workspace_root=self.workspace,
            output_jsonl=output,
            split_salt="intake-fixture-v1",
        )

        records = [
            json.loads(line)
            for line in output.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [record["source_id"] for record in records],
            ["a-human", "z-synthetic"],
        )
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["labels"], {"human": 1, "synthetic": 1})
        self.assertRegex(summary["manifest_hash"], r"^[0-9a-f]{64}$")

    def test_manifest_assembly_rejects_changed_source(self) -> None:
        source = self._input("human.txt", _long_text("human"))
        result = register_human_source(
            source, workspace_root=self.workspace,
            source_id="human-1", language="en", genre="news",
            source_group_id="group", reference="https://example.test/human",
            retrieved_at="2026-08-02T10:00:00Z", acquisition_method="export",
        )
        result.text_path.write_text("tampered", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "content SHA-256"):
            assemble_source_manifest(
                workspace_root=self.workspace,
                output_jsonl=self.root / "manifest.jsonl",
                split_salt="intake-fixture-v1",
            )

        self.assertFalse((self.root / "manifest.jsonl").exists())

    def test_manifest_assembly_rejects_changed_synthetic_prompt(self) -> None:
        source = self._input("synthetic.txt", _long_text("synthetic"))
        prompt = self._input("prompt.txt", "Original prompt.")
        result = register_synthetic_source(
            source, prompt, workspace_root=self.workspace,
            source_id="synthetic-1", language="en", genre="news",
            source_group_id="group", generated_at="2026-08-02T10:00:00Z",
            provider="provider", model="model", model_version="snapshot",
            generation_parameters={"temperature": 0},
        )
        result.prompt_path.write_text("Changed prompt.", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "prompt SHA-256"):
            assemble_source_manifest(
                workspace_root=self.workspace,
                output_jsonl=self.root / "manifest.jsonl",
                split_salt="intake-fixture-v1",
            )

        self.assertFalse((self.root / "manifest.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
