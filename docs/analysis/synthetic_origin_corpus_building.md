# Synthetic-origin corpus building

## Purpose

Calibration labels must be supported by provenance rather than inferred from
writing style or filenames. The offline corpus builder turns immutable UTF-8
text files plus a strict JSONL source manifest into the calibration corpus
accepted by `structural-en-v0.1`.

The builder does not download sources and does not generate synthetic text. A
human reviewer first preserves each source text and the evidence that supports
its label. The builder then verifies bytes, performs conservative
normalization, rejects duplicates, assigns source groups to splits and emits a
hash-bound receipt.

Use `docs/analysis/synthetic_origin_corpus_intake.md` to register exact text,
prompts and generation logs and assemble this manifest without manually
calculating hashes. The builder remains independently usable with any manifest
that satisfies the same contract.

## Source record contract

Each manifest line is exactly one `synthetic-origin-source-record@1` object:

```json
{
  "schema": "synthetic-origin-source-record@1",
  "source_id": "human-news-0001",
  "label": "human",
  "language": "en",
  "genre": "news",
  "source_group_id": "publisher-story-2026-0001",
  "text_path": "human/news-0001.txt",
  "content_sha256": "<lowercase SHA-256 of the exact file bytes>",
  "provenance": {
    "kind": "human_source",
    "reference": "https://publisher.example/article",
    "retrieved_at": "2026-08-02T10:00:00Z",
    "acquisition_method": "publisher-export"
  }
}
```

Human provenance requires `kind=human_source`, a reference, an RFC 3339
retrieval time and an acquisition method. A human label means that the source
has affirmative human-origin evidence; it must not be assigned merely because
no generator is known.

Synthetic records use the same top-level fields and require:

```json
{
  "kind": "generator",
  "reference": "generation-log:batch-2026-08-02/item-0042",
  "generated_at": "2026-08-02T10:00:00Z",
  "provider": "provider-name",
  "model": "model-name",
  "model_version": "exact-version-or-snapshot",
  "prompt_sha256": "<lowercase SHA-256 of the exact prompt artifact>",
  "generation_parameters": {
    "temperature": 0.7
  }
}
```

`reference` must point to a preserved source or generation record. A URL alone
is useful provenance, but long-term reproducibility also requires retaining
the licensed source snapshot or generation log outside the corpus.

`text_path` is relative to `--source-root`. Absolute paths, traversal outside
that root, missing files, non-UTF-8 input, NUL characters and files larger than
2 MB are rejected. The declared `content_sha256` must match the exact bytes.

## Normalization and deduplication

`utf8-nfc-lines-v1` performs only these transformations:

- removes an optional UTF-8 byte-order mark;
- applies Unicode NFC;
- converts CRLF and CR to LF;
- removes trailing whitespace from lines and surrounding whitespace.

The original byte hash and normalized-text hash are copied into each sample's
provenance. The original file is never modified.

The builder rejects exact duplicates after case-folding and whitespace
canonicalization. For texts of at least 50 tokens it also applies a scalable
64-bit SimHash candidate search and rejects pairs with similar length and a
Hamming distance of at most three. This is a contamination guard, not a proof
that all paraphrases have been found. Semantic and cross-language leakage still
requires review.

## Split assignment

Every `source_group_id` is assigned as a unit by SHA-256 over the public
`split-salt` and group identity. The default buckets are 60% `train`, 20%
`calibration` and 20% `test`. The same group, salt and ratios always receive the
same split, independent of manifest line order.

Put revisions, excerpts, prompt variants and texts derived from one underlying
source in the same group. A group may contain both human and synthetic items,
but it cannot mix language or genre. Hash bucketing is stable rather than
balance-forcing, so a small manifest may fail final corpus validation when one
split lacks either label. Add independent source groups; do not hand-move a
sample after observing its score.

Changing the salt or ratios defines a new dataset version. Never choose them to
improve evaluation results.

## Command

```powershell
& ".\.venv\Scripts\python.exe" main.py build-synthetic-corpus `
    --manifest-jsonl ".\data\calibration\sources\manifest.jsonl" `
    --source-root ".\data\calibration\sources\text" `
    --output-jsonl ".\data\calibration\synthetic-origin-en-v1.jsonl" `
    --receipt-json ".\data\calibration\synthetic-origin-en-v1.build.json" `
    --split-salt "synthetic-origin-en-v1"
```

Both output paths must be new. The corpus and receipt are staged and installed
without replacing existing files. The builder validates the completed corpus
through the same public loader used by calibration before publishing it.

The `synthetic-origin-corpus-build@1` receipt binds the canonical manifest,
source byte hashes, normalization and deduplication policies, split policy,
corpus and split hashes, output-file hash and its own receipt hash. Keep the
manifest, source files, corpus and receipt together as one immutable release.

Reconstruct the build at any later time:

```powershell
& ".\.venv\Scripts\python.exe" main.py verify-synthetic-corpus-build `
    --manifest-jsonl ".\data\calibration\sources\manifest.jsonl" `
    --source-root ".\data\calibration\sources\text" `
    --corpus-jsonl ".\data\calibration\synthetic-origin-en-v1.jsonl" `
    --receipt-json ".\data\calibration\synthetic-origin-en-v1.build.json"
```

Verification checks the receipt's self-hash, exact policy versions, every
source file, the deterministic split assignment, canonical corpus bytes, and
all corpus and split hashes. It performs no writes.

After building, run `validate-synthetic-corpus`. Calibration and held-out
evaluation remain separate steps.
