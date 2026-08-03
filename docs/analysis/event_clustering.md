# Event Clustering

## Status

Planned. Argus does not currently create event clusters or issue same-event
decisions.

The implemented `compare-document-event-similarity` command exposes three
uncalibrated document-pair signals: publication-time proximity, overlap of
reviewed resolved entities, and lexical similarity. It always reports
`same_event_decision=none`.

## Required boundary

Event clustering must not assume that one document equals one event. News
bulletins, live blogs, timelines, and retrospective articles may contain many
events; short notices may contain none that can be reconstructed safely.
Segmentation or event-candidate extraction is therefore required before a
document-level heuristic can become clustering evidence.

Before implementation, the method must define:

- the event-candidate unit and its exact source spans;
- time, place, participant, action, and object observations;
- treatment of ongoing processes and updates;
- missing-signal behaviour;
- group-level provenance and cluster revision history;
- calibration data separated by topic, language, source, and time;
- merge, split, abstention, and human-review rules.

An event cluster will remain an evidence-backed reconstruction, not proof that
all attached claims are true.

## Implemented fragment boundary

Argus can persist source-anchored event-fragment candidates for one immutable
document version and one text-derived artifact. Each candidate records a
half-open character span, a SHA-256 digest of the selected text, the method and
method version, author, rationale, and explicit quality limitations. Reads
recompute the digest against the source text and fail closed on disagreement.

Candidates may overlap and alternative methods may produce different spans.
They are not segmentation runs, do not assert complete document coverage, and
carry no event or cluster assignment.

## Deterministic structural proposal

`inspect-document-text` exposes the selected immutable text artifact as exact
non-empty paragraph blocks. Each line includes its half-open offsets, text
digest, escaped preview and whether it satisfies the documented heading-like
shape. Output truncation affects only the preview, never the offsets or digest.
If a document version has several supported text artifacts, the operator must
select one explicitly rather than allowing Argus to combine them.

`segment-event-fragments` implements the first conservative boundary proposer.
It uses only blank-line paragraph structure and repeated heading-like blocks.
The first adjacent title/section-heading pair is treated as document title plus
first section, avoiding a title-only fragment. When no repeatable internal
heading boundary exists, the method abstains from internal splitting and emits
one whole-content fallback candidate with an explicit limitation.

The command is read-only by default. `--persist` stores the exact proposals via
the existing candidate contract and is idempotent for the same artifact,
offsets, method and method version. Neither mode creates an `Event`, assigns a
cluster, claims that every candidate describes one event, or claims that the
document was completely or correctly segmented. A first-class
`SegmentationRun`, richer boundary evidence and calibrated evaluation remain
planned.
