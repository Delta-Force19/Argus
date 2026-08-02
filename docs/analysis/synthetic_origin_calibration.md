# Synthetic-origin text calibration

## Purpose

The structural text-origin method is experimental. Its score is not a
probability and cannot identify a human author. Before Argus may attach a
stronger interpretation to a later method version, the method must be measured
against an independently labelled corpus with visible error rates.

The calibration harness keeps three concerns separate:

1. `train` may be used to develop a future method;
2. `calibration` selects a threshold without seeing final performance;
3. `test` measures the already selected threshold and must remain untouched.

## Corpus contract

The corpus is UTF-8 JSONL. Every line is exactly one
`synthetic-origin-calibration-sample@1` object with these fields:

- `sample_id`: stable unique identity;
- `label`: `human` or `synthetic`;
- `split`: `train`, `calibration` or `test`;
- `language`: BCP 47 language identifier;
- `genre`: explicit comparison stratum such as `news` or `essay`;
- `source_group_id`: related items that must never cross splits;
- `text`: exact analyzed text;
- `provenance`: source or generator evidence with label-matching `kind` and a
  non-blank `reference`.

The first harness accepts English because it evaluates
`structural-en-v0.1`. Every split must contain both labels. Duplicate sample
identities and duplicate text are rejected. A `source_group_id` cannot appear
in multiple splits, preventing prompt families, document revisions or source
clusters from leaking into held-out evaluation.

Each sample, split and full corpus receives a canonical SHA-256 identity. File
line order does not affect these hashes; any material sample or split change
does.

## Threshold decision

`calibrate-synthetic-origin` evaluates only the `calibration` split. It selects
maximum balanced accuracy and breaks ties in favour of the lower false-positive
rate, then the higher threshold. The canonical
`synthetic-origin-threshold@1` decision binds:

- method, exact method version and Argus software version;
- corpus and calibration-split hashes;
- selected threshold and objective;
- calibration metrics and limitations;
- its own verification hash.

The threshold is an operating cutoff on an uncalibrated detector score. It is
not a conversion from score to probability.

## Held-out evaluation

`evaluate-synthetic-origin` accepts only an intact threshold decision bound to
the exact corpus. It evaluates only `test` and emits
`synthetic-origin-evaluation@1` with:

- confusion matrix;
- false-positive and false-negative rates;
- sensitivity, specificity, accuracy and balanced accuracy;
- ROC AUC;
- Wilson 95% intervals for rates;
- language and genre slices;
- immutable input hashes and a report hash.

A result is marked `sufficient_sample_size=true` only with at least 30 human
and 30 synthetic items in that population. This is a visibility floor, not a
claim that 60 samples are enough for deployment.

## Commands

```powershell
& ".\.venv\Scripts\python.exe" main.py validate-synthetic-corpus `
    --input-jsonl ".\data\calibration\synthetic-origin-en.jsonl"

& ".\.venv\Scripts\python.exe" main.py calibrate-synthetic-origin `
    --input-jsonl ".\data\calibration\synthetic-origin-en.jsonl" `
    --output-json ".\data\calibration\structural-en-v0.1-threshold.json"

& ".\.venv\Scripts\python.exe" main.py evaluate-synthetic-origin `
    --input-jsonl ".\data\calibration\synthetic-origin-en.jsonl" `
    --threshold-json ".\data\calibration\structural-en-v0.1-threshold.json" `
    --output-json ".\data\calibration\structural-en-v0.1-test-report.json"
```

Output paths must be new. Existing decisions and reports are never silently
overwritten.

## Interpretation boundary

Evaluation can show that a method is unsuitable. Good aggregate results do not
justify deployment when relevant genres, languages, generators, editing
conditions or source populations are absent. No report produced by this
harness changes the current analysis conclusion from `inconclusive`; enabling
stronger conclusions requires a new, explicitly reviewed method version.
