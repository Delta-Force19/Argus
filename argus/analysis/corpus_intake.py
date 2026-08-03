from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping
from urllib.parse import urlsplit

from argus.analysis.corpus_builder import (
    MAX_SOURCE_BYTES,
    SOURCE_RECORD_SCHEMA,
    build_corpus_from_manifest,
)
from argus.analysis.synthetic_origin import StructuralSyntheticTextAnalyzer


GENERATION_LOG_SCHEMA = "synthetic-origin-generation-log@1"
INTAKE_VERSION = "provenance-intake-v0.2"
SUPPORTED_GENERATION_INTAKE_VERSIONS = {
    "provenance-intake-v0.1",
    INTAKE_VERSION,
}
MAX_PROMPT_BYTES = 2_000_000
_SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_HUMAN_TEXT_SCOPES = {
    "article-body",
    "article-body-with-headline",
    "full-document",
    "publisher-export",
}


@dataclass(frozen=True, slots=True)
class IntakeRegistration:
    source_id: str
    label: str
    text_path: Path
    record_path: Path
    content_sha256: str
    prompt_path: Path | None = None
    generation_log_path: Path | None = None


@dataclass(frozen=True, slots=True)
class IntakeInspection:
    records: int
    groups: int
    labels: dict[str, int]
    splits: dict[str, int]
    split_labels: dict[str, dict[str, int]]
    sources: tuple[dict[str, object], ...]
    missing_split_labels: tuple[str, ...]
    ineligible_sources: tuple[str, ...]
    unsupported_language_sources: tuple[str, ...]
    ready_for_build: bool


def register_human_source(
        input_text: Path,
        *,
        workspace_root: Path,
        source_id: str,
        language: str,
        genre: str,
        source_group_id: str,
        reference: str,
        title: str,
        author: str,
        publisher: str,
        published_date: str,
        text_scope: str,
        retrieved_at: str,
        acquisition_method: str,
) -> IntakeRegistration:
    """Preserve one affirmatively human-authored source and its provenance."""

    _validate_identifier(source_id)
    raw = _load_utf8_artifact(input_text, kind="source text", limit=MAX_SOURCE_BYTES)
    _validate_common_metadata(
        language=language,
        genre=genre,
        source_group_id=source_group_id,
        reference=reference,
    )
    _validate_timestamp(retrieved_at, field="retrieved_at")
    _validate_publication_reference(reference)
    for field, value in (
            ("title", title), ("author", author), ("publisher", publisher)):
        _require_text(value, field=field)
    _validate_date(published_date, field="published_date")
    if text_scope not in _HUMAN_TEXT_SCOPES:
        raise ValueError(
            "text_scope must be one of: "
            + ", ".join(sorted(_HUMAN_TEXT_SCOPES)) + "."
        )
    _require_text(acquisition_method, field="acquisition_method")
    content_hash = sha256(raw).hexdigest()
    relative_text = f"human/{source_id}.txt"
    record = {
        "schema": SOURCE_RECORD_SCHEMA,
        "source_id": source_id,
        "label": "human",
        "language": language.strip().lower(),
        "genre": genre.strip().lower(),
        "source_group_id": source_group_id.strip(),
        "text_path": relative_text,
        "content_sha256": content_hash,
        "provenance": {
            "kind": "human_source",
            "reference": reference.strip(),
            "title": title.strip(),
            "author": author.strip(),
            "publisher": publisher.strip(),
            "published_date": published_date,
            "text_scope": text_scope,
            "retrieved_at": retrieved_at,
            "acquisition_method": acquisition_method.strip(),
        },
    }
    text_path = workspace_root / "text" / relative_text
    record_path = workspace_root / "records" / f"{source_id}.json"
    _install_artifacts({
        text_path: raw,
        record_path: _json_bytes(record),
    })
    return IntakeRegistration(
        source_id=source_id,
        label="human",
        text_path=text_path,
        record_path=record_path,
        content_sha256=content_hash,
    )


def register_synthetic_source(
        input_text: Path,
        prompt_file: Path,
        *,
        workspace_root: Path,
        source_id: str,
        language: str,
        genre: str,
        source_group_id: str,
        generated_at: str,
        provider: str,
        model: str,
        model_version: str,
        generation_parameters: Mapping[str, object],
) -> IntakeRegistration:
    """Preserve one synthetic source, its prompt and a hash-bound generation log."""

    _validate_identifier(source_id)
    raw = _load_utf8_artifact(input_text, kind="source text", limit=MAX_SOURCE_BYTES)
    prompt = _load_utf8_artifact(
        prompt_file, kind="prompt", limit=MAX_PROMPT_BYTES
    )
    _validate_common_metadata(
        language=language,
        genre=genre,
        source_group_id=source_group_id,
        reference="generation-log",
    )
    _validate_timestamp(generated_at, field="generated_at")
    for field, value in (
            ("provider", provider), ("model", model),
            ("model_version", model_version)):
        _require_text(value, field=field)
    parameters = _canonical_mapping(generation_parameters)
    content_hash = sha256(raw).hexdigest()
    prompt_hash = sha256(prompt).hexdigest()
    relative_text = f"synthetic/{source_id}.txt"
    relative_prompt = f"prompts/{source_id}.txt"
    relative_log = f"generation-logs/{source_id}.json"
    log: dict[str, object] = {
        "schema": GENERATION_LOG_SCHEMA,
        "intake_version": INTAKE_VERSION,
        "source_id": source_id,
        "generated_at": generated_at,
        "provider": provider.strip(),
        "model": model.strip(),
        "model_version": model_version.strip(),
        "generation_parameters": parameters,
        "text_path": relative_text,
        "content_sha256": content_hash,
        "prompt_path": relative_prompt,
        "prompt_sha256": prompt_hash,
    }
    log["log_hash"] = _hash_json(log)
    record = {
        "schema": SOURCE_RECORD_SCHEMA,
        "source_id": source_id,
        "label": "synthetic",
        "language": language.strip().lower(),
        "genre": genre.strip().lower(),
        "source_group_id": source_group_id.strip(),
        "text_path": relative_text,
        "content_sha256": content_hash,
        "provenance": {
            "kind": "generator",
            "reference": f"generation-log:{relative_log}",
            "generated_at": generated_at,
            "provider": provider.strip(),
            "model": model.strip(),
            "model_version": model_version.strip(),
            "prompt_sha256": prompt_hash,
            "generation_parameters": parameters,
        },
    }
    text_path = workspace_root / "text" / relative_text
    prompt_path = workspace_root / relative_prompt
    log_path = workspace_root / relative_log
    record_path = workspace_root / "records" / f"{source_id}.json"
    _install_artifacts({
        text_path: raw,
        prompt_path: prompt,
        log_path: _json_bytes(log),
        record_path: _json_bytes(record),
    })
    return IntakeRegistration(
        source_id=source_id,
        label="synthetic",
        text_path=text_path,
        prompt_path=prompt_path,
        generation_log_path=log_path,
        record_path=record_path,
        content_sha256=content_hash,
    )


def assemble_source_manifest(
        *,
        workspace_root: Path,
        output_jsonl: Path,
        split_salt: str,
        train_ratio: float = 0.6,
        calibration_ratio: float = 0.2,
) -> dict[str, object]:
    """Assemble sorted sidecars and validate them through the corpus builder."""

    if output_jsonl.exists():
        raise FileExistsError(f"Output already exists: {output_jsonl}")
    records = _load_intake_records(workspace_root)
    manifest_bytes = b"".join(_json_line_bytes(record) for record in records)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    temporary = _stage_file(output_jsonl, manifest_bytes)
    try:
        build = build_corpus_from_manifest(
            temporary,
            source_root=workspace_root / "text",
            split_salt=split_salt,
            train_ratio=train_ratio,
            calibration_ratio=calibration_ratio,
        )
        os.link(temporary, output_jsonl)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema": SOURCE_RECORD_SCHEMA,
        "intake_version": INTAKE_VERSION,
        "records": len(records),
        "labels": build.receipt["labels"],
        "languages": build.receipt["languages"],
        "genres": build.receipt["genres"],
        "splits": build.receipt["splits"],
        "manifest_hash": build.receipt["manifest_hash"],
    }


def inspect_source_intake(
        *,
        workspace_root: Path,
        split_salt: str,
        train_ratio: float = 0.6,
        calibration_ratio: float = 0.2,
) -> IntakeInspection:
    """Verify intake artifacts and report build readiness without publishing."""

    records = _load_intake_records(workspace_root)
    manifest_bytes = b"".join(_json_line_bytes(record) for record in records)
    with tempfile.TemporaryDirectory(prefix="argus-corpus-inspection-") as directory:
        manifest_path = Path(directory) / "manifest.jsonl"
        manifest_path.write_bytes(manifest_bytes)
        build = build_corpus_from_manifest(
            manifest_path,
            source_root=workspace_root / "text",
            split_salt=split_salt,
            train_ratio=train_ratio,
            calibration_ratio=calibration_ratio,
        )

    split_names = ("train", "calibration", "test")
    label_names = ("human", "synthetic")
    split_labels = {
        split: {label: 0 for label in label_names} for split in split_names
    }
    analyzer = StructuralSyntheticTextAnalyzer()
    sources: list[dict[str, object]] = []
    ineligible: list[str] = []
    unsupported_languages: list[str] = []
    for sample in build.samples:
        source_id = str(sample["sample_id"])
        label = str(sample["label"])
        split = str(sample["split"])
        language = str(sample["language"])
        assessment = analyzer.analyze(str(sample["text"]))
        split_labels[split][label] += 1
        if not assessment.eligible_for_scoring:
            ineligible.append(source_id)
        if not (language == "en" or language.startswith("en-")):
            unsupported_languages.append(source_id)
        sources.append({
            "source_id": source_id,
            "label": label,
            "split": split,
            "source_group_id": str(sample["source_group_id"]),
            "language": language,
            "genre": str(sample["genre"]),
            "eligible_for_scoring": assessment.eligible_for_scoring,
            "word_count": assessment.word_count,
            "sentence_count": assessment.sentence_count,
        })
    missing = tuple(
        f"{split}:{label}"
        for split in split_names
        for label in label_names
        if split_labels[split][label] == 0
    )
    groups = {str(sample["source_group_id"]) for sample in build.samples}
    return IntakeInspection(
        records=len(build.samples),
        groups=len(groups),
        labels=dict(sorted(Counter(
            str(sample["label"]) for sample in build.samples
        ).items())),
        splits=dict(sorted(Counter(
            str(sample["split"]) for sample in build.samples
        ).items())),
        split_labels=split_labels,
        sources=tuple(sources),
        missing_split_labels=missing,
        ineligible_sources=tuple(sorted(ineligible)),
        unsupported_language_sources=tuple(sorted(unsupported_languages)),
        ready_for_build=not missing and not ineligible and not unsupported_languages,
    )


def _load_intake_records(workspace_root: Path) -> list[dict[str, object]]:
    records_root = workspace_root / "records"
    if not records_root.is_dir():
        raise ValueError("Intake workspace contains no records directory.")
    paths = sorted(records_root.glob("*.json"))
    if not paths:
        raise ValueError("Intake workspace contains no source records.")
    records: list[dict[str, object]] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid source record: {path.name}.") from error
        if not isinstance(value, dict):
            raise ValueError(f"Source record {path.name} must be a JSON object.")
        if value.get("source_id") != path.stem:
            raise ValueError(f"Source record {path.name} does not match its filename.")
        _verify_intake_record(value, workspace_root=workspace_root)
        records.append(value)
    ids = [str(value.get("source_id", "")) for value in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Intake workspace contains duplicate source_id values.")
    records.sort(key=lambda value: str(value.get("source_id", "")))
    return records


def _verify_intake_record(
        record: Mapping[str, object], *, workspace_root: Path,
) -> None:
    if record.get("label") == "human":
        _verify_human_intake_record(record)
        return
    if record.get("label") != "synthetic":
        raise ValueError("Intake record label must be human or synthetic.")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Synthetic intake record provenance must be an object.")
    reference = provenance.get("reference")
    prefix = "generation-log:"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ValueError("Synthetic intake record must reference a generation log.")
    log_path = _safe_workspace_path(
        workspace_root, reference.removeprefix(prefix), kind="generation log"
    )
    try:
        log_value = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Synthetic generation log is missing or invalid.") from error
    if not isinstance(log_value, dict):
        raise ValueError("Synthetic generation log must be a JSON object.")
    log = dict(log_value)
    stored_hash = log.pop("log_hash", None)
    if stored_hash != _hash_json(log):
        raise ValueError("Synthetic generation log hash verification failed.")
    expected_log_fields = {
        "schema", "intake_version", "source_id", "generated_at", "provider",
        "model", "model_version", "generation_parameters", "text_path",
        "content_sha256", "prompt_path", "prompt_sha256",
    }
    if set(log) != expected_log_fields \
            or log.get("schema") != GENERATION_LOG_SCHEMA \
            or log.get("intake_version") not in SUPPORTED_GENERATION_INTAKE_VERSIONS:
        raise ValueError("Synthetic generation log contract is invalid.")
    prompt_path = _safe_workspace_path(
        workspace_root, log.get("prompt_path"), kind="prompt"
    )
    prompt = _load_utf8_artifact(
        prompt_path, kind="prompt", limit=MAX_PROMPT_BYTES
    )
    if sha256(prompt).hexdigest() != log.get("prompt_sha256"):
        raise ValueError("Synthetic prompt SHA-256 does not match generation log.")
    comparisons = {
        "source_id": record.get("source_id"),
        "text_path": record.get("text_path"),
        "content_sha256": record.get("content_sha256"),
        "generated_at": provenance.get("generated_at"),
        "provider": provenance.get("provider"),
        "model": provenance.get("model"),
        "model_version": provenance.get("model_version"),
        "prompt_sha256": provenance.get("prompt_sha256"),
        "generation_parameters": provenance.get("generation_parameters"),
    }
    mismatched = sorted(
        field for field, expected in comparisons.items()
        if log.get(field) != expected
    )
    if mismatched:
        raise ValueError(
            "Synthetic generation log disagrees with source record: "
            + ", ".join(mismatched) + "."
        )


def _verify_human_intake_record(record: Mapping[str, object]) -> None:
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Human intake record provenance must be an object.")
    required = {
        "kind", "reference", "title", "author", "publisher",
        "published_date", "text_scope", "retrieved_at", "acquisition_method",
    }
    missing = sorted(required - set(provenance))
    if missing:
        raise ValueError(
            "Human intake record provenance is missing: "
            + ", ".join(missing) + "."
        )
    if provenance.get("kind") != "human_source":
        raise ValueError("Human intake record provenance kind is invalid.")
    for field in ("title", "author", "publisher", "acquisition_method"):
        _require_text(provenance.get(field), field=field)
    reference = provenance.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string.")
    _validate_publication_reference(reference)
    published_date = provenance.get("published_date")
    if not isinstance(published_date, str):
        raise ValueError("published_date must be a string.")
    _validate_date(published_date, field="published_date")
    text_scope = provenance.get("text_scope")
    if text_scope not in _HUMAN_TEXT_SCOPES:
        raise ValueError("Human intake record text_scope is invalid.")
    retrieved_at = provenance.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        raise ValueError("retrieved_at must be a string.")
    _validate_timestamp(retrieved_at, field="retrieved_at")


def _safe_workspace_path(root: Path, value: object, *, kind: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Synthetic {kind} path must be non-blank.")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"Synthetic {kind} path must be relative.")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError(f"Synthetic {kind} path escapes intake workspace.")
    return path


def _validate_identifier(value: str) -> None:
    if not isinstance(value, str) or not _SOURCE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "source_id must use 1-100 lowercase ASCII letters, digits, '.', '_' or '-'."
        )


def _validate_common_metadata(
        *, language: str, genre: str, source_group_id: str, reference: str,
) -> None:
    for field, value in (
            ("language", language), ("genre", genre),
            ("source_group_id", source_group_id), ("reference", reference)):
        _require_text(value, field=field)


def _require_text(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank.")


def _validate_timestamp(value: str, *, field: str) -> None:
    _require_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be RFC 3339.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone.")


def _validate_date(value: str, *, field: str) -> None:
    _require_text(value, field=field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 calendar date.") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use YYYY-MM-DD format.")


def _validate_publication_reference(value: str) -> None:
    _require_text(value, field="reference")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("reference must be an absolute HTTP(S) publication URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("reference must not contain URL credentials.")


def _load_utf8_artifact(path: Path, *, kind: str, limit: int) -> bytes:
    if not path.is_file():
        raise ValueError(f"{kind} file does not exist: {path}")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"{kind} exceeds {limit} bytes.")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{kind} must be valid UTF-8.") from error
    if "\x00" in decoded or not decoded.strip():
        raise ValueError(f"{kind} must contain non-empty text without NUL characters.")
    return raw


def _canonical_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("generation_parameters must be an object.")
    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "generation_parameters must contain finite JSON values."
        ) from error
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("generation_parameters must be an object.")
    return decoded


def _install_artifacts(artifacts: Mapping[Path, bytes]) -> None:
    targets = list(artifacts)
    if len({path.resolve() for path in targets}) != len(targets):
        raise ValueError("Intake artifact paths must be distinct.")
    existing = [path for path in targets if path.exists()]
    if existing:
        raise FileExistsError(f"Intake artifact already exists: {existing[0]}")
    staged: dict[Path, Path] = {}
    installed: list[Path] = []
    try:
        for target, content in artifacts.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            staged[target] = _stage_file(target, content)
        for target, temporary in staged.items():
            os.link(temporary, target)
            installed.append(target)
    except Exception:
        for target in installed:
            target.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _stage_file(target: Path, content: bytes) -> Path:
    with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def _json_line_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _hash_json(value: Mapping[str, object]) -> str:
    return sha256(_json_line_bytes(value).rstrip(b"\n")).hexdigest()
