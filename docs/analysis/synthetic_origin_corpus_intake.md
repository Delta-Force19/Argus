# Synthetic-origin corpus intake

## Purpose

Corpus intake preserves evidence before the immutable manifest and calibration
corpus are built. It computes hashes from exact supplied bytes and creates
strict sidecar records; it does not infer authorship from writing style, a
filename, or missing generator metadata.

One versioned workspace contains:

```text
data/calibration/sources/
  text/human/
  text/synthetic/
  prompts/
  generation-logs/
  records/
```

Every registration is create-only. Reusing a `source_id` fails without
replacing preserved files. Identifiers are restricted to lowercase ASCII
letters, digits, `.`, `_` and `-` and cannot express filesystem traversal.

## Register a human source

The operator must have affirmative human-authorship evidence and a durable
reference to the preserved publication or archive record.

```powershell
$intake = ".\data\calibration\sources"

& ".\.venv\Scripts\python.exe" main.py register-human-corpus-source `
    --input-text ".\incoming\human-news-0001.txt" `
    --workspace-root $intake `
    --source-id "human-news-0001" `
    --language "en" `
    --genre "news" `
    --source-group-id "publisher-story-2026-0001" `
    --reference "https://publisher.example/article" `
    --retrieved-at "2026-08-02T10:00:00Z" `
    --acquisition-method "publisher-export"
```

The command copies exact bytes to `text/human/` and creates one
`synthetic-origin-source-record@1` sidecar under `records/`. It does not rewrite
line endings or replace the supplied input file.

## Register a synthetic source

Preserve the exact generated text and prompt. Record provider, model,
resolvable model version or snapshot, time and all known parameters.

```powershell
& ".\.venv\Scripts\python.exe" main.py register-synthetic-corpus-source `
    --input-text ".\incoming\synthetic-news-0001.txt" `
    --prompt-file ".\incoming\synthetic-news-0001.prompt.txt" `
    --workspace-root $intake `
    --source-id "synthetic-news-0001" `
    --language "en" `
    --genre "news" `
    --source-group-id "prompt-family-2026-0001" `
    --generated-at "2026-08-02T10:05:00Z" `
    --provider "provider-name" `
    --model "model-name" `
    --model-version "exact-snapshot" `
    --generation-parameters-json '{"temperature":0.7,"seed":42}'
```

This creates text, prompt, a self-hashed
`synthetic-origin-generation-log@1`, and the source-record sidecar as one
transactional registration. Non-finite JSON values are rejected. Put prompt
variants or generations derived from one underlying source in the same
`source_group_id` so related observations remain in one split.

## Assemble the manifest

After registering independent sources, assemble a sorted manifest:

```powershell
& ".\.venv\Scripts\python.exe" main.py assemble-synthetic-corpus-manifest `
    --workspace-root $intake `
    --output-jsonl "$intake\manifest.jsonl" `
    --split-salt "synthetic-origin-en-v1"
```

The output must be new. Before publishing it, the command runs the full builder
validation: exact source hashes, strict provenance, group dimensions,
duplicate guards and deterministic split assignment. A small intake may still
fail final calibration-corpus validation if both labels are not represented in
every split; add independent source groups rather than tuning the salt after
observing results.

Pass the resulting `manifest.jsonl` unchanged to `build-synthetic-corpus`.
Treat released registration records as reviewed evidence: correct mistakes in
a new dataset version rather than silently editing released artifacts.
