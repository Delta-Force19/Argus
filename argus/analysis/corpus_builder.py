from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from argus.analysis.calibration import (
    SAMPLE_SCHEMA,
    CalibrationLabel,
    CalibrationSplit,
    load_calibration_corpus,
)


SOURCE_RECORD_SCHEMA = "synthetic-origin-source-record@1"
BUILD_RECEIPT_SCHEMA = "synthetic-origin-corpus-build@1"
BUILDER_VERSION = "source-manifest-v0.1"
NORMALIZATION_POLICY = "utf8-nfc-lines-v1"
DEDUPLICATION_POLICY = "canonical-exact+simhash64-lsh4x16-hamming3-v1"
MAX_SOURCE_BYTES = 2_000_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    label: CalibrationLabel
    language: str
    genre: str
    source_group_id: str
    text_path: str
    content_sha256: str
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class LoadedSource:
    record: SourceRecord
    original_sha256: str
    normalized_text_sha256: str
    deduplication_sha256: str
    text: str
    simhash64: int
    token_count: int


@dataclass(frozen=True, slots=True)
class CorpusBuild:
    samples: tuple[dict[str, object], ...]
    receipt: dict[str, object]


def build_corpus_from_manifest(
        manifest_path: Path,
        *,
        source_root: Path,
        split_salt: str,
        train_ratio: float = 0.6,
        calibration_ratio: float = 0.2,
) -> CorpusBuild:
    """Validate source records and assemble a deterministic corpus build."""

    _validate_split_policy(
        split_salt=split_salt,
        train_ratio=train_ratio,
        calibration_ratio=calibration_ratio,
    )
    records = _load_manifest(manifest_path)
    loaded = tuple(
        _load_source(record, source_root=source_root) for record in records
    )
    _validate_loaded_sources(loaded)
    split_by_group = {
        group_id: _assign_split(
            group_id,
            split_salt=split_salt,
            train_ratio=train_ratio,
            calibration_ratio=calibration_ratio,
        )
        for group_id in sorted({item.record.source_group_id for item in loaded})
    }
    samples = tuple(
        _make_sample(item, split=split_by_group[item.record.source_group_id])
        for item in sorted(loaded, key=lambda value: value.record.source_id)
    )
    manifest_hash = _hash_json([
        _canonical_source_record(item.record) for item in sorted(
            loaded, key=lambda value: value.record.source_id
        )
    ])
    receipt: dict[str, object] = {
        "schema": BUILD_RECEIPT_SCHEMA,
        "builder_version": BUILDER_VERSION,
        "source_record_schema": SOURCE_RECORD_SCHEMA,
        "sample_schema": SAMPLE_SCHEMA,
        "manifest_hash": manifest_hash,
        "normalization_policy": NORMALIZATION_POLICY,
        "deduplication_policy": DEDUPLICATION_POLICY,
        "split_policy": {
            "algorithm": "sha256-group-bucket-v1",
            "salt": split_salt,
            "train_ratio": train_ratio,
            "calibration_ratio": calibration_ratio,
            "test_ratio": round(1.0 - train_ratio - calibration_ratio, 10),
        },
        "source_records": len(loaded),
        "source_groups": len(split_by_group),
        "labels": dict(sorted(Counter(
            item.record.label.value for item in loaded
        ).items())),
        "languages": dict(sorted(Counter(
            item.record.language for item in loaded
        ).items())),
        "genres": dict(sorted(Counter(
            item.record.genre for item in loaded
        ).items())),
        "splits": dict(sorted(Counter(
            split_by_group[item.record.source_group_id].value for item in loaded
        ).items())),
        "source_content_hashes": {
            item.record.source_id: item.original_sha256 for item in sorted(
                loaded, key=lambda value: value.record.source_id
            )
        },
    }
    receipt["build_input_hash"] = _hash_json(receipt)
    return CorpusBuild(samples=samples, receipt=receipt)


def write_corpus_build(
        build: CorpusBuild,
        *,
        output_jsonl: Path,
        receipt_json: Path,
) -> dict[str, object]:
    """Write new corpus artifacts, validate them, and bind a build receipt."""

    if output_jsonl.resolve() == receipt_json.resolve():
        raise ValueError("Corpus and receipt paths must be different.")
    for path in (output_jsonl, receipt_json):
        if path.exists():
            raise FileExistsError(f"Output already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    corpus_text = _serialize_samples(build.samples)
    corpus_temp = _write_temporary(output_jsonl, corpus_text)
    receipt_temp: Path | None = None
    corpus_installed = False
    receipt_installed = False
    try:
        corpus = load_calibration_corpus(corpus_temp)
        receipt = _finalize_receipt(
            build, corpus_hash=corpus.corpus_hash,
            split_hashes=corpus.split_hashes, corpus_text=corpus_text,
        )
        receipt_text = json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"
        receipt_temp = _write_temporary(receipt_json, receipt_text)
        _install_new_file(corpus_temp, output_jsonl)
        corpus_installed = True
        _install_new_file(receipt_temp, receipt_json)
        receipt_installed = True
        return receipt
    except Exception:
        if corpus_installed and not receipt_installed:
            output_jsonl.unlink(missing_ok=True)
        raise
    finally:
        corpus_temp.unlink(missing_ok=True)
        if receipt_temp is not None:
            receipt_temp.unlink(missing_ok=True)


def verify_corpus_build(
        manifest_path: Path,
        *,
        source_root: Path,
        corpus_path: Path,
        receipt_path: Path,
) -> dict[str, object]:
    """Rebuild and verify one corpus, source set, and receipt without writes."""

    try:
        receipt_value = json.loads(
            receipt_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError("Build receipt must be valid JSON.") from error
    if not isinstance(receipt_value, dict):
        raise ValueError("Build receipt must contain one JSON object.")
    receipt = dict(receipt_value)
    stored_hash = receipt.get("receipt_hash")
    unhashed = dict(receipt)
    unhashed.pop("receipt_hash", None)
    if stored_hash != _hash_json(unhashed):
        raise ValueError("Build receipt hash verification failed.")
    if receipt.get("schema") != BUILD_RECEIPT_SCHEMA \
            or receipt.get("builder_version") != BUILDER_VERSION:
        raise ValueError("Build receipt schema or builder version is unsupported.")
    if receipt.get("normalization_policy") != NORMALIZATION_POLICY \
            or receipt.get("deduplication_policy") != DEDUPLICATION_POLICY:
        raise ValueError("Build receipt policy version is unsupported.")
    split_policy = receipt.get("split_policy")
    if not isinstance(split_policy, dict) or set(split_policy) != {
        "algorithm", "salt", "train_ratio", "calibration_ratio", "test_ratio",
    } or split_policy.get("algorithm") != "sha256-group-bucket-v1":
        raise ValueError("Build receipt split policy is invalid.")
    build = build_corpus_from_manifest(
        manifest_path,
        source_root=source_root,
        split_salt=split_policy["salt"],
        train_ratio=split_policy["train_ratio"],
        calibration_ratio=split_policy["calibration_ratio"],
    )
    expected_text = _serialize_samples(build.samples)
    try:
        actual_text = corpus_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Built corpus is not valid UTF-8.") from error
    if actual_text != expected_text:
        raise ValueError("Built corpus does not match the verified source manifest.")
    corpus = load_calibration_corpus(corpus_path)
    expected_receipt = _finalize_receipt(
        build, corpus_hash=corpus.corpus_hash,
        split_hashes=corpus.split_hashes, corpus_text=actual_text,
    )
    if receipt != expected_receipt:
        raise ValueError("Build receipt does not match reconstructed corpus inputs.")
    return expected_receipt


def _load_manifest(path: Path) -> tuple[SourceRecord, ...]:
    records: list[SourceRecord] = []
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON on manifest line {line_number}: {error.msg}."
            ) from error
        records.append(_parse_source_record(value, line_number=line_number))
    if not records:
        raise ValueError("Source manifest contains no records.")
    ids = [record.source_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Source manifest contains duplicate source_id values.")
    return tuple(records)


def _parse_source_record(value: object, *, line_number: int) -> SourceRecord:
    if not isinstance(value, dict):
        raise ValueError(f"Manifest line {line_number} must be a JSON object.")
    expected = {
        "schema", "source_id", "label", "language", "genre",
        "source_group_id", "text_path", "content_sha256", "provenance",
    }
    if set(value) != expected or value.get("schema") != SOURCE_RECORD_SCHEMA:
        raise ValueError(
            f"Manifest line {line_number} does not match {SOURCE_RECORD_SCHEMA}."
        )
    strings: dict[str, str] = {}
    for field in (
            "source_id", "language", "genre", "source_group_id", "text_path",
            "content_sha256"):
        item = value[field]
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Manifest line {line_number} field {field} must be non-blank."
            )
        strings[field] = item.strip()
    if not _SHA256_PATTERN.fullmatch(strings["content_sha256"]):
        raise ValueError(
            f"Manifest line {line_number} content_sha256 must be lowercase SHA-256."
        )
    try:
        label = CalibrationLabel(value["label"])
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Manifest line {line_number} label must be human or synthetic."
        ) from error
    provenance = value["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError(
            f"Manifest line {line_number} provenance must be an object."
        )
    _validate_provenance(provenance, label=label, line_number=line_number)
    return SourceRecord(
        source_id=strings["source_id"],
        label=label,
        language=strings["language"].lower(),
        genre=strings["genre"].lower(),
        source_group_id=strings["source_group_id"],
        text_path=strings["text_path"],
        content_sha256=strings["content_sha256"],
        provenance=dict(provenance),
    )


def _validate_provenance(
        provenance: Mapping[str, object],
        *,
        label: CalibrationLabel,
        line_number: int,
) -> None:
    common = {"kind", "reference"}
    human = {"retrieved_at", "acquisition_method"}
    synthetic = {
        "generated_at", "provider", "model", "model_version",
        "prompt_sha256", "generation_parameters",
    }
    required = common | (synthetic if label is CalibrationLabel.SYNTHETIC else human)
    missing = sorted(required - set(provenance))
    if missing:
        raise ValueError(
            f"Manifest line {line_number} provenance is missing: {', '.join(missing)}."
        )
    expected_kind = (
        "generator" if label is CalibrationLabel.SYNTHETIC else "human_source"
    )
    if provenance.get("kind") != expected_kind:
        raise ValueError(
            f"Manifest line {line_number} provenance kind must be {expected_kind!r}."
        )
    for field in required - {"generation_parameters"}:
        item = provenance.get(field)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Manifest line {line_number} provenance {field} must be non-blank."
            )
    timestamp_field = (
        "generated_at" if label is CalibrationLabel.SYNTHETIC else "retrieved_at"
    )
    _validate_timestamp(str(provenance[timestamp_field]), line_number=line_number)
    if label is CalibrationLabel.SYNTHETIC:
        if not _SHA256_PATTERN.fullmatch(str(provenance["prompt_sha256"])):
            raise ValueError(
                f"Manifest line {line_number} prompt_sha256 must be lowercase SHA-256."
            )
        if not isinstance(provenance["generation_parameters"], dict):
            raise ValueError(
                f"Manifest line {line_number} generation_parameters must be an object."
            )


def _validate_timestamp(value: str, *, line_number: int) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"Manifest line {line_number} timestamp must be RFC 3339."
        ) from error
    if parsed.tzinfo is None:
        raise ValueError(
            f"Manifest line {line_number} timestamp must include a timezone."
        )


def _load_source(record: SourceRecord, *, source_root: Path) -> LoadedSource:
    root = source_root.resolve()
    relative = Path(record.text_path)
    if relative.is_absolute():
        raise ValueError(f"Source {record.source_id} text_path must be relative.")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Source {record.source_id} escapes source-root.")
    if not path.is_file():
        raise ValueError(f"Source {record.source_id} text file does not exist.")
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError(f"Source {record.source_id} exceeds {MAX_SOURCE_BYTES} bytes.")
    original_hash = sha256(raw).hexdigest()
    if original_hash != record.content_sha256:
        raise ValueError(f"Source {record.source_id} content SHA-256 does not match.")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"Source {record.source_id} is not valid UTF-8.") from error
    text = _normalize_text(decoded)
    if not text:
        raise ValueError(f"Source {record.source_id} is empty after normalization.")
    canonical = " ".join(_TOKEN_PATTERN.findall(text.casefold()))
    deduplication_hash = sha256(canonical.encode("utf-8")).hexdigest()
    tokens = canonical.split()
    return LoadedSource(
        record=record,
        original_sha256=original_hash,
        normalized_text_sha256=sha256(text.encode("utf-8")).hexdigest(),
        deduplication_sha256=deduplication_hash,
        text=text,
        simhash64=_simhash64(tokens),
        token_count=len(tokens),
    )


def _normalize_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Source text contains a NUL character.")
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _validate_loaded_sources(sources: Sequence[LoadedSource]) -> None:
    exact: dict[str, str] = {}
    group_dimensions: dict[str, tuple[str, str]] = {}
    for item in sources:
        previous = exact.setdefault(
            item.deduplication_sha256, item.record.source_id
        )
        if previous != item.record.source_id:
            raise ValueError(
                f"Sources {previous} and {item.record.source_id} contain "
                "duplicate text."
            )
        dimensions = (item.record.language, item.record.genre)
        prior_dimensions = group_dimensions.setdefault(
            item.record.source_group_id, dimensions
        )
        if prior_dimensions != dimensions:
            raise ValueError(
                f"Source group {item.record.source_group_id} mixes language or genre."
            )
    band_index: dict[tuple[int, int], list[LoadedSource]] = {}
    for item in sources:
        if item.token_count < 50:
            continue
        candidates: dict[str, LoadedSource] = {}
        for band in range(4):
            value = (item.simhash64 >> (band * 16)) & 0xFFFF
            for candidate in band_index.get((band, value), []):
                candidates[candidate.record.source_id] = candidate
        for candidate in candidates.values():
            ratio = min(item.token_count, candidate.token_count) / max(
                item.token_count, candidate.token_count
            )
            distance = (item.simhash64 ^ candidate.simhash64).bit_count()
            if ratio >= 0.9 and distance <= 3:
                raise ValueError(
                    f"Sources {candidate.record.source_id} and "
                    f"{item.record.source_id} are near-duplicate text."
                )
        for band in range(4):
            value = (item.simhash64 >> (band * 16)) & 0xFFFF
            band_index.setdefault((band, value), []).append(item)


def _make_sample(
        source: LoadedSource,
        *,
        split: CalibrationSplit,
) -> dict[str, object]:
    provenance = dict(source.record.provenance)
    provenance.update({
        "source_record_schema": SOURCE_RECORD_SCHEMA,
        "source_id": source.record.source_id,
        "original_sha256": source.original_sha256,
        "normalized_text_sha256": source.normalized_text_sha256,
        "normalization_policy": NORMALIZATION_POLICY,
    })
    return {
        "schema": SAMPLE_SCHEMA,
        "sample_id": source.record.source_id,
        "label": source.record.label.value,
        "split": split.value,
        "language": source.record.language,
        "genre": source.record.genre,
        "source_group_id": source.record.source_group_id,
        "text": source.text,
        "provenance": provenance,
    }


def _assign_split(
        source_group_id: str,
        *,
        split_salt: str,
        train_ratio: float,
        calibration_ratio: float,
) -> CalibrationSplit:
    digest = sha256(f"{split_salt}\0{source_group_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest, "big") / (1 << 256)
    if bucket < train_ratio:
        return CalibrationSplit.TRAIN
    if bucket < train_ratio + calibration_ratio:
        return CalibrationSplit.CALIBRATION
    return CalibrationSplit.TEST


def _validate_split_policy(
        *,
        split_salt: str,
        train_ratio: float,
        calibration_ratio: float,
) -> None:
    if not isinstance(split_salt, str) or not split_salt.strip() \
            or len(split_salt) > 100:
        raise ValueError("split_salt must contain 1 to 100 characters.")
    for name, value in (
            ("train_ratio", train_ratio),
            ("calibration_ratio", calibration_ratio)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not 0 < float(value) < 1:
            raise ValueError(f"{name} must be between zero and one.")
    if train_ratio + calibration_ratio >= 1:
        raise ValueError("Split ratios must leave a non-zero test ratio.")


def _simhash64(tokens: Sequence[str]) -> int:
    if not tokens:
        return 0
    features = tokens if len(tokens) < 5 else [
        "\0".join(tokens[index:index + 5])
        for index in range(len(tokens) - 4)
    ]
    weights = [0] * 64
    for feature in features:
        value = int.from_bytes(
            sha256(feature.encode("utf-8")).digest()[:8], "big"
        )
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            result |= 1 << bit
    return result


def _canonical_source_record(record: SourceRecord) -> dict[str, object]:
    return {
        "schema": SOURCE_RECORD_SCHEMA,
        "source_id": record.source_id,
        "label": record.label.value,
        "language": record.language,
        "genre": record.genre,
        "source_group_id": record.source_group_id,
        "text_path": record.text_path,
        "content_sha256": record.content_sha256,
        "provenance": record.provenance,
    }


def _write_temporary(target: Path, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _install_new_file(temporary: Path, target: Path) -> None:
    """Install without overwriting a path created by a concurrent process."""

    os.link(temporary, target)
    temporary.unlink()


def _serialize_samples(samples: Sequence[Mapping[str, object]]) -> str:
    return "".join(
        json.dumps(sample, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False) + "\n"
        for sample in samples
    )


def _finalize_receipt(
        build: CorpusBuild,
        *,
        corpus_hash: str,
        split_hashes: Mapping[str, str],
        corpus_text: str,
) -> dict[str, object]:
    receipt = dict(build.receipt)
    receipt.update({
        "corpus_hash": corpus_hash,
        "split_hashes": dict(split_hashes),
        "corpus_file_sha256": sha256(corpus_text.encode("utf-8")).hexdigest(),
    })
    receipt["receipt_hash"] = _hash_json(receipt)
    return receipt


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"Non-finite JSON number {value} is not allowed.")


def _hash_json(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
