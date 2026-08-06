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

## Deterministic boundary proposal

`inspect-document-text` exposes the selected immutable text artifact as exact
non-empty paragraph blocks. Each line includes its half-open offsets, text
digest, escaped preview and whether it satisfies the documented heading-like
shape. Output truncation affects only the preview, never the offsets or digest.
If a document version has several supported text artifacts, the operator must
select one explicitly rather than allowing Argus to combine them.

`segment-event-fragments` implements the first conservative boundary proposer.
For ordinary text it uses blank-line paragraph structure and repeated
heading-like blocks. The first adjacent title/section-heading pair is treated
as document title plus first section, avoiding a title-only fragment. When no
repeatable internal heading boundary exists, the structural method abstains
from internal splitting and emits one whole-content fallback candidate.

For a transcript with validated cue provenance, method
`deterministic-cue-gap-segmentation` instead proposes a boundary before each
contributing cue whose gap from the preceding contributing cue is at least
2200 milliseconds. It uses the cue output ranges to preserve exact half-open
text offsets. The threshold is a conservative initial calibration, not a
semantic decision: shorter editorial transitions can be missed and a long
pause inside one story can still produce a false boundary. Both limitations
are attached to every proposal.

The command is read-only by default. `--persist` stores the exact proposals via
the existing candidate contract and is idempotent for the same artifact,
offsets, method and method version. Neither mode creates an `Event`, assigns a
cluster, claims that every candidate describes one event, or claims that the
document was completely or correctly segmented. A first-class
`SegmentationRun`, richer boundary evidence and calibrated evaluation remain
planned.

## Implemented observation extraction

`extract-event-observations` consumes one explicit persisted fragment set and
analyzes each exact source span independently. The first method,
`spacy-event-observations`, records six narrow signal types:

- named-entity mentions that may indicate a participant, place, time or named
  event;
- non-auxiliary verbal tokens as action candidates;
- dependency-labeled complements as grammatical object candidates.

Every observation retains its absolute half-open offsets, surface text,
normalized value, original model or dependency label, rationale, fragment
identifier, model package version and explicit limitations. The complete run
is an immutable `EVENT_OBSERVATIONS` derived artifact; relational
`EventObservationCandidate` rows are a queryable projection of that payload.
Preview is read-only and persistence is explicit and idempotent.

These signals do not establish semantic roles or factual relations. In
particular, a person mentioned in a fragment is not automatically an event
participant, a location mention is not automatically the event location, and
a grammatical subject or object does not prove who acted on what. The method
does not resolve coreference or create subject-predicate-object triples. It
creates no `Event`, claim, cluster or assignment. Those decisions require
cross-source evidence and later calibrated reconstruction.

## Implemented fragment profiles

`profile-event-fragments` consumes one exact persisted `EVENT_OBSERVATIONS`
artifact. The versioned deterministic profiler retains named-entity signals,
uses separate English and Russian generic-action lists, filters non-lexical
and low-information action candidates, and admits grammatical objects only
when their dependency head is a noun or proper noun, their subtree remains
within explicit size limits, and a determiner or possessive is not attached to
a versioned vague nominal head. Exact repeated type/value pairs are grouped
while preserving every source observation identifier, surface form,
occurrence count and source range.

Every raw observation is accounted for exactly once. An observation either
contributes to one grouped signal or receives a stable exclusion code such as
`generic_action`, `pronominal_object`, `vague_object` or `oversized_object`,
plus a rationale.
Preview is read-only. `--persist` stores the complete retained and excluded
decision set as an immutable `EVENT_FRAGMENT_PROFILES` derived artifact and is
idempotent for the same input and method version. `--show-exclusions` exposes
the candidate-level audit without making the default CLI output needlessly
large.

The profile is a comparison input, not an event reconstruction. Lexical
filters can omit meaningful context, exact normalization does not resolve
aliases, retained mentions have no proven semantic role, and no cross-source
identity or event assignment is created.

## Implemented fragment-pair candidates

`compare-event-fragments` consumes one exact immutable
`EVENT_FRAGMENT_PROFILES` artifact and audits every unordered fragment pair.
For each pair it records every exact shared type/value signal, both sets of
source observation identifiers, the evidence type and an explicit heuristic
point value. Missing overlap is recorded as `insufficient`; it is not treated
as evidence that two fragments describe different events.

The version 1 candidate rule requires shared values in at least two distinct
observation types, including at least one participant, place, time or named
event mention. Pairs satisfying that rule are `candidate`; narrower overlap is
`weak`. The labels prioritize later review only. They are not probabilities,
clusters, links, same-event decisions or factual assertions. Exact matching
also misses aliases and paraphrases and can overstate recurring topic terms.

Preview is read-only. `--persist` stores the complete pair audit as an
immutable `EVENT_FRAGMENT_PAIR_CANDIDATES` artifact bound to the parent
artifact identifier and content hash. `--show-matches` exposes the
source-level evidence behind each status.

## Implemented cluster proposals

`propose-event-fragment-clusters` consumes one exact immutable
`EVENT_FRAGMENT_PAIR_CANDIDATES` artifact and treats only `candidate` pairs as
edges in a review graph. A proposal is a maximal clique of at least two
fragments: every pair inside it must independently have candidate status, and
no additional fragment can be added without breaking that all-pairs rule.

This deliberately differs from connected-component clustering. If `1↔3` and
`2↔3` are candidates while `1↔2` is weak, the method emits the overlapping
alternatives `{1,3}` and `{2,3}` rather than transitively merging `{1,2,3}`.
The containing graph component is marked `ambiguous`, and the `1↔2` decision
is preserved as an explicit blocking pair. Complete candidate components are
`coherent`; fragments with no candidate edge are `isolated`.

The labels and proposals organize review. They are not a partition,
probability, same-event assertion or negative event-identity decision. Weak
and insufficient pairs prevent automatic clique expansion under this method
but do not prove that the fragments describe different events. Preview is
read-only. `--persist` stores an immutable
`EVENT_FRAGMENT_CLUSTER_PROPOSALS` artifact bound to the exact parent artifact
identifier and content hash. No `Event`, cluster row or assignment is created.

## Event-content readiness

Event analysis must distinguish source content from page metadata. Text
extracted from the HTML surrounding an audiovisual item is not evidence that
the speech or events in the item were captured. When a document URI identifies
a video page, Argus requires a `transcript` artifact before that text may be
persisted as event-fragment candidates or used for document-pair similarity.

Short length and missing paragraph separators remain warnings rather than hard
failures: a brief article can still be complete. Readiness therefore blocks
only the demonstrable source-medium mismatch, while exposing weaker quality
signals for review.
