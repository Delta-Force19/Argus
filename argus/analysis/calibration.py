from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path

from argus.analysis.synthetic_origin import StructuralSyntheticTextAnalyzer


SAMPLE_SCHEMA = "synthetic-origin-calibration-sample@1"
THRESHOLD_SCHEMA = "synthetic-origin-threshold@1"
EVALUATION_SCHEMA = "synthetic-origin-evaluation@1"
METHOD_NAME = "synthetic-origin-text"
METHOD_VERSION = "structural-en-v0.1"


class CalibrationLabel(str, Enum):
    HUMAN = "human"
    SYNTHETIC = "synthetic"


class CalibrationSplit(str, Enum):
    TRAIN = "train"
    CALIBRATION = "calibration"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    sample_id: str
    label: CalibrationLabel
    split: CalibrationSplit
    language: str
    genre: str
    source_group_id: str
    text: str
    provenance: dict[str, object]
    sample_hash: str


@dataclass(frozen=True, slots=True)
class CalibrationCorpus:
    samples: tuple[CalibrationSample, ...]
    corpus_hash: str
    split_hashes: dict[str, str]


def load_calibration_corpus(path: Path) -> CalibrationCorpus:
    """Load and strictly validate an immutable JSONL calibration corpus."""

    samples: list[CalibrationSample] = []
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON on corpus line {line_number}: {error.msg}."
            ) from error
        samples.append(_parse_sample(value, line_number=line_number))
    if not samples:
        raise ValueError("Calibration corpus contains no samples.")
    _validate_corpus(samples)
    ordered = tuple(sorted(samples, key=lambda item: item.sample_id))
    split_hashes = {
        split.value: _hash_json([
            item.sample_hash for item in ordered if item.split is split
        ])
        for split in CalibrationSplit
    }
    corpus_hash = _hash_json({
        "schema": SAMPLE_SCHEMA,
        "sample_hashes": [item.sample_hash for item in ordered],
        "split_hashes": split_hashes,
    })
    return CalibrationCorpus(
        samples=ordered,
        corpus_hash=corpus_hash,
        split_hashes=split_hashes,
    )


def corpus_summary(corpus: CalibrationCorpus) -> dict[str, object]:
    analyzer = StructuralSyntheticTextAnalyzer()
    eligible = sum(
        analyzer.analyze(sample.text).eligible_for_scoring
        for sample in corpus.samples
    )
    return {
        "schema": SAMPLE_SCHEMA,
        "corpus_hash": corpus.corpus_hash,
        "samples": len(corpus.samples),
        "eligible_samples": eligible,
        "labels": dict(sorted(Counter(
            sample.label.value for sample in corpus.samples
        ).items())),
        "splits": dict(sorted(Counter(
            sample.split.value for sample in corpus.samples
        ).items())),
        "languages": dict(sorted(Counter(
            sample.language for sample in corpus.samples
        ).items())),
        "genres": dict(sorted(Counter(
            sample.genre for sample in corpus.samples
        ).items())),
        "split_hashes": corpus.split_hashes,
    }


def calibrate_threshold(
        corpus: CalibrationCorpus,
        *,
        software_version: str,
) -> dict[str, object]:
    """Select a threshold on calibration only, favouring fewer false positives."""

    _validate_software_version(software_version)
    rows = _score_split(corpus, CalibrationSplit.CALIBRATION)
    candidates = sorted({score for _, score in rows})
    candidates.extend([0.0, 1.0])
    best_threshold = 0.0
    best_metrics: dict[str, object] | None = None
    best_key: tuple[float, float, float] | None = None
    for threshold in sorted(set(candidates)):
        metrics = _binary_metrics(rows, threshold)
        key = (
            float(metrics["balanced_accuracy"]),
            -float(metrics["false_positive_rate"]["rate"]),
            threshold,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_threshold = threshold
            best_metrics = metrics
    assert best_metrics is not None
    payload: dict[str, object] = {
        "schema": THRESHOLD_SCHEMA,
        "method": METHOD_NAME,
        "method_version": METHOD_VERSION,
        "software_version": software_version,
        "corpus_hash": corpus.corpus_hash,
        "calibration_split_hash": corpus.split_hashes["calibration"],
        "threshold": round(best_threshold, 4),
        "selection_objective": "maximum_balanced_accuracy",
        "tie_break": "lowest_false_positive_rate_then_highest_threshold",
        "calibration_metrics": best_metrics,
        "limitations": [
            "The detector score is not a probability.",
            "The test split was not used to select this threshold.",
            "Threshold validity is limited to represented languages and genres.",
        ],
    }
    payload["decision_hash"] = _hash_json(payload)
    return payload


def evaluate_test_split(
        corpus: CalibrationCorpus,
        threshold_decision: Mapping[str, object],
        *,
        software_version: str,
) -> dict[str, object]:
    """Evaluate one preselected threshold against the untouched test split."""

    _validate_software_version(software_version)
    _validate_threshold_decision(
        corpus, threshold_decision, software_version=software_version
    )
    threshold = float(threshold_decision["threshold"])
    rows = _score_split(corpus, CalibrationSplit.TEST)
    overall = _binary_metrics(rows, threshold)
    samples = [
        sample for sample in corpus.samples
        if sample.split is CalibrationSplit.TEST
    ]
    scored = {sample.sample_id: score for sample, score in rows}
    slices: dict[str, dict[str, object]] = {}
    for dimension in ("language", "genre"):
        values = sorted({getattr(sample, dimension) for sample in samples})
        slices[dimension] = {}
        for value in values:
            slice_rows = [
                (sample, scored[sample.sample_id])
                for sample in samples if getattr(sample, dimension) == value
            ]
            slices[dimension][value] = _binary_metrics(
                slice_rows, threshold
            )
    payload: dict[str, object] = {
        "schema": EVALUATION_SCHEMA,
        "method": METHOD_NAME,
        "method_version": METHOD_VERSION,
        "software_version": software_version,
        "corpus_hash": corpus.corpus_hash,
        "test_split_hash": corpus.split_hashes["test"],
        "threshold_decision_hash": threshold_decision["decision_hash"],
        "threshold": threshold,
        "score_is_probability": False,
        "overall": overall,
        "slices": slices,
        "limitations": [
            "Results apply only to the immutable test split and represented "
            "conditions.",
            "Small slices have wide uncertainty and are marked insufficient.",
            "This evaluation does not establish human authorship for any document.",
        ],
    }
    payload["report_hash"] = _hash_json(payload)
    return payload


def write_canonical_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_sample(value: object, *, line_number: int) -> CalibrationSample:
    if not isinstance(value, dict):
        raise ValueError(f"Corpus line {line_number} must be a JSON object.")
    expected = {
        "schema", "sample_id", "label", "split", "language", "genre",
        "source_group_id", "text", "provenance",
    }
    if set(value) != expected:
        raise ValueError(
            f"Corpus line {line_number} fields do not match {SAMPLE_SCHEMA}."
        )
    if value["schema"] != SAMPLE_SCHEMA:
        raise ValueError(f"Unsupported schema on corpus line {line_number}.")
    strings = {}
    for field in (
            "sample_id", "language", "genre", "source_group_id", "text"):
        item = value[field]
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Corpus line {line_number} field {field} must be non-blank."
            )
        strings[field] = item.strip() if field != "text" else item
    try:
        label = CalibrationLabel(value["label"])
        split = CalibrationSplit(value["split"])
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid label or split on corpus line {line_number}."
        ) from error
    provenance = value["provenance"]
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError(
            f"Corpus line {line_number} provenance must be a non-empty object."
        )
    provenance_kind = provenance.get("kind")
    provenance_reference = provenance.get("reference")
    expected_kind = (
        "generator"
        if label is CalibrationLabel.SYNTHETIC else "human_source"
    )
    if provenance_kind != expected_kind:
        raise ValueError(
            f"Corpus line {line_number} provenance kind must be "
            f"{expected_kind!r} for label {label.value!r}."
        )
    if (
        not isinstance(provenance_reference, str)
        or not provenance_reference.strip()
    ):
        raise ValueError(
            f"Corpus line {line_number} provenance reference must be non-blank."
        )
    canonical = dict(value)
    sample_hash = _hash_json(canonical)
    return CalibrationSample(
        sample_id=strings["sample_id"],
        label=label,
        split=split,
        language=strings["language"].lower(),
        genre=strings["genre"].lower(),
        source_group_id=strings["source_group_id"],
        text=strings["text"],
        provenance=dict(provenance),
        sample_hash=sample_hash,
    )


def _validate_corpus(samples: Sequence[CalibrationSample]) -> None:
    ids = [sample.sample_id for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("Calibration corpus contains duplicate sample_id values.")
    text_hashes = [sha256(sample.text.encode("utf-8")).hexdigest()
                   for sample in samples]
    if len(text_hashes) != len(set(text_hashes)):
        raise ValueError("Calibration corpus contains duplicate text content.")
    group_splits: dict[str, CalibrationSplit] = {}
    for sample in samples:
        if not (sample.language == "en" or sample.language.startswith("en-")):
            raise ValueError(
                "structural-en-v0.1 corpus samples must be English."
            )
        previous = group_splits.setdefault(sample.source_group_id, sample.split)
        if previous is not sample.split:
            raise ValueError(
                "One source_group_id cannot cross corpus splits."
            )
    for split in CalibrationSplit:
        labels = {
            sample.label for sample in samples if sample.split is split
        }
        if labels != set(CalibrationLabel):
            raise ValueError(
                f"Split {split.value} must contain human and synthetic samples."
            )


def _score_split(
        corpus: CalibrationCorpus,
        split: CalibrationSplit,
) -> list[tuple[CalibrationSample, float]]:
    analyzer = StructuralSyntheticTextAnalyzer()
    rows: list[tuple[CalibrationSample, float]] = []
    for sample in corpus.samples:
        if sample.split is not split:
            continue
        assessment = analyzer.analyze(sample.text)
        if not assessment.eligible_for_scoring or assessment.detector_score is None:
            raise ValueError(
                f"Sample {sample.sample_id} is ineligible for structural-en-v0.1."
            )
        rows.append((sample, assessment.detector_score))
    return rows


def _binary_metrics(
        rows: Sequence[tuple[CalibrationSample, float]],
        threshold: float,
) -> dict[str, object]:
    tp = fp = tn = fn = 0
    human_scores: list[float] = []
    synthetic_scores: list[float] = []
    for sample, score in rows:
        predicted = score >= threshold
        actual = sample.label is CalibrationLabel.SYNTHETIC
        if actual:
            synthetic_scores.append(score)
            tp += int(predicted)
            fn += int(not predicted)
        else:
            human_scores.append(score)
            fp += int(predicted)
            tn += int(not predicted)
    fpr = _rate(fp, fp + tn)
    tpr = _rate(tp, tp + fn)
    specificity = _rate(tn, tn + fp)
    accuracy = _rate(tp + tn, len(rows))
    balanced = (float(tpr["rate"]) + float(specificity["rate"])) / 2
    return {
        "samples": len(rows),
        "human_samples": len(human_scores),
        "synthetic_samples": len(synthetic_scores),
        "sufficient_sample_size": (
            len(human_scores) >= 30 and len(synthetic_scores) >= 30
        ),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "false_positive_rate": fpr,
        "false_negative_rate": _rate(fn, fn + tp),
        "true_positive_rate": tpr,
        "specificity": specificity,
        "accuracy": accuracy,
        "balanced_accuracy": round(balanced, 6),
        "roc_auc": _roc_auc(human_scores, synthetic_scores),
    }


def _rate(numerator: int, denominator: int) -> dict[str, object]:
    if denominator == 0:
        return {"numerator": numerator, "denominator": denominator,
                "rate": 0.0, "wilson_95": None}
    rate = numerator / denominator
    z = 1.959963984540054
    center = (rate + z * z / (2 * denominator)) / (
        1 + z * z / denominator
    )
    margin = z * sqrt(
        rate * (1 - rate) / denominator
        + z * z / (4 * denominator * denominator)
    ) / (1 + z * z / denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(rate, 6),
        "wilson_95": [round(max(0.0, center - margin), 6),
                      round(min(1.0, center + margin), 6)],
    }


def _roc_auc(human: Sequence[float], synthetic: Sequence[float]) -> float | None:
    if not human or not synthetic:
        return None
    wins = 0.0
    for synthetic_score in synthetic:
        for human_score in human:
            wins += synthetic_score > human_score
            wins += 0.5 * (synthetic_score == human_score)
    return round(wins / (len(human) * len(synthetic)), 6)


def _validate_threshold_decision(
        corpus: CalibrationCorpus,
        decision: Mapping[str, object],
        *,
        software_version: str,
) -> None:
    required = {
        "schema", "method", "method_version", "software_version", "corpus_hash",
        "calibration_split_hash", "threshold", "selection_objective",
        "tie_break", "calibration_metrics", "limitations", "decision_hash",
    }
    if set(decision) != required:
        raise ValueError("Threshold decision fields are invalid.")
    if decision["schema"] != THRESHOLD_SCHEMA:
        raise ValueError("Threshold decision schema is unsupported.")
    if (
        decision["method"] != METHOD_NAME
        or decision["method_version"] != METHOD_VERSION
    ):
        raise ValueError("Threshold decision method does not match evaluator.")
    if decision["software_version"] != software_version:
        raise ValueError("Threshold decision software version does not match.")
    if decision["corpus_hash"] != corpus.corpus_hash:
        raise ValueError("Threshold decision corpus hash does not match.")
    if decision["calibration_split_hash"] != corpus.split_hashes["calibration"]:
        raise ValueError("Threshold decision calibration split hash does not match.")
    threshold = decision["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) \
            or not 0 <= float(threshold) <= 1:
        raise ValueError("Threshold must be between zero and one.")
    unhashed = dict(decision)
    stored_hash = unhashed.pop("decision_hash")
    if stored_hash != _hash_json(unhashed):
        raise ValueError("Threshold decision hash verification failed.")


def _hash_json(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _validate_software_version(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("software_version must contain 1 to 100 characters.")
