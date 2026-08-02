# Argus Data Model

## Purpose

This document defines the core entities used by Argus and the relationships between them.

The data model separates:

1. observations collected from sources;
2. claims made by people and organizations;
3. reconstructed real-world events;
4. analytical inferences produced by Argus.

Argus does not store an absolute representation of truth. It stores observations, evidence, claims, relationships and explicitly qualified inferences.

---

## Knowledge Layers

Argus represents information through three connected layers.

### Reality Layer

Represents the best currently supported model of real-world entities, events and relationships.

Examples:

- people;
- organizations;
- countries;
- locations;
- agreements;
- elections;
- military actions;
- economic changes;
- historical events.

The Reality Layer must distinguish confirmed information from disputed or uncertain information.

### Information Layer

Represents how the world is described by sources.

It stores:

- documents;
- statements;
- claims;
- quotations;
- frames;
- rhetorical features;
- narratives;
- source attribution.

Multiple incompatible descriptions of the same event may coexist in this layer.

### Reasoning Layer

Represents analyses and hypotheses produced by Argus.

It stores:

- inferred relationships;
- factual consistency assessments;
- possible effects;
- potential beneficiaries;
- communication-function hypotheses;
- competing explanations;
- supporting and contradicting evidence.

Every reasoning object must be traceable to its evidence.

---

## Core Entities

### Source

An origin from which a document is obtained.

Examples:

- newspaper;
- news agency;
- government department;
- international organization;
- research institution;
- individual social-media account.

Important properties:

- name;
- source type;
- jurisdictions and operating countries;
- publication languages;
- ownership;
- affiliations;
- official status;
- known relationships with other sources;
- temporal validity of source metadata.

Country, ownership, affiliation, publication language and intended audience are
separate properties. They may change over time and must not be collapsed into
a single permanent source label.

A source profile provides context. It must not automatically determine whether its claims are true.

---

### Document

A discrete information artifact collected by Argus.

Examples:

- article;
- speech transcript;
- press release;
- treaty;
- report;
- court decision;
- social-media post;
- video transcript.

Important properties:

- source;
- author or speaker;
- publication time or time interval;
- collection time;
- original language using a BCP 47 identifier;
- canonical URL or archive reference;
- raw content;
- normalized content;
- content hash;
- document type.

A document is evidence that particular information was published. It is not evidence that every statement inside it is true.
A translation is a derived artifact. It must retain a relationship to the
original document and record the translation method, model, version, and
creation time. Analytical evidence should reference the original-language
content whenever possible.

---

### Observation

A directly recorded property of a document or external data source.

Examples:

- a sentence appears in a document;
- a word is used 14 times;
- an article was published at a particular time;
- a value appears in an official statistical table;
- a headline changed between archived versions.

Observations should be reproducible and minimally interpretive.

Important properties:

- observation type;
- observed value;
- document or dataset reference;
- exact location within the source;
- extraction method;
- method version;
- timestamp.

### Entity Mention

An `EntityMention` is a model observation that a text span appears to refer to
a person, organization, location or another typed object. It is not yet a
canonical real-world entity.

Each mention records:

- the exact `DocumentVersion`;
- the immutable derived text artifact used as input;
- start-inclusive and end-exclusive character offsets;
- the original surface text;
- a deterministic Unicode- and whitespace-normalized form;
- the model's original label and Argus's normalized mention type;
- the recognition method, model version and result-schema version through its
  parent `ENTITY_MENTIONS` derived artifact;
- explicit quality limitations.

The parent artifact also records the exact input artifact identifier and
content hash. Queryable mention rows are an index of that immutable result.
Argus does not merge equal normalized strings, assign a knowledge-base
identity, or infer that two mentions refer to the same object at this stage.

---

### Entity Candidate

An `EntityCandidate` is a queryable projection of one mention that a versioned
canonicalization method considers suitable for later identity resolution. It
is still not a canonical real-world entity and does not imply that equal
canonical strings share identity.

The parent `ENTITY_CANDIDATES` derived artifact records:

- the exact `ENTITY_MENTIONS` artifact identifier and content hash;
- the recognition method and model version that produced the input;
- the canonicalization method, method version and result-schema version;
- one ordered decision for every input mention;
- whether the mention is an identity-resolution candidate;
- the explicit exclusion reason for values, temporal expressions and
  unsupported types;
- the exact source-text context and offsets for accepted candidates;
- explicit quality limitations.

The first deterministic canonicalizer accepts person, organization, location,
group, facility, product, event, work, law and language mentions. Date, time,
percent, money, quantity, ordinal and cardinal mentions remain available in the
raw `EntityMention` layer but are excluded from entity identity resolution.
Unknown types are excluded rather than guessed.

Canonical text applies only conservative Unicode, whitespace and case
normalization. It does not expand abbreviations, translate names, lemmatize
nationalities, repair NER labels, assign external identifiers or merge aliases.
Consequently, `António Guterres` and `Guterres`, or `US` and `United States`,
remain separate candidates until a later evidence-bearing resolution stage.

Queryable `EntityCandidate` rows contain only accepted decisions and link back
to the exact `EntityMention`, `DocumentVersion` and candidate artifact. A new
canonicalization version produces a new immutable artifact and projection,
preserving earlier results.

---

### Alias Proposal

An `AliasProposal` is a versioned, evidence-bearing suggestion that two
different candidate forms within one immutable `ENTITY_CANDIDATES` artifact
may refer to the same real-world object. It is neither an approved alias nor a
resolved `Entity`.

The parent `ALIAS_PROPOSALS` derived artifact records:

- the exact candidate artifact identifier and content hash;
- the candidate canonicalization method and version used as input;
- the deterministic proposal method, method version and schema version;
- both referenced `EntityCandidate` identifiers and canonical forms;
- the transparent signal type;
- occurrence counts and representative source contexts;
- a heuristic confidence score, its explicit basis and a textual rationale;
- explicit quality limitations.

The first proposer recognizes only three conservative co-occurrence signals:
acronyms, full-versus-short person names and simple English plural variants for
groups. It rejects a compact form that is already a token in the longer form,
so a pair such as `UN` and `UN News` is not proposed merely because their
initials coincide.

Confidence scores rank deterministic signals within this method version. They
are not calibrated probabilities and must not be interpreted as the
probability that two forms share identity. A new proposer version produces a
new immutable artifact and projection, preserving the earlier proposals.

The initial foundation compares forms only when they co-occur inside one exact
candidate artifact and therefore share a `DocumentVersion`. Corpus-wide
similarity without co-occurrence, external identifiers, multilingual aliases,
human approval, entity creation and merge history remain later resolution
stages.

---

### Alias Decision

An `AliasDecision` is an append-only human review event for one exact
`AliasProposal`. Its status is `approved`, `rejected` or `needs_review`, and
every decision requires both a non-blank reason and a reviewer identifier.

Decisions form an explicit revision chain. The first decision for a proposal
has revision 1 and no predecessor. A later decision receives the next revision
and references the exact decision it supersedes; the earlier row is never
updated or deleted. This preserves disagreements, corrections and changing
evidence instead of presenting the latest judgment as if it had always been
known.

Approval remains deliberately separate from identity resolution. An approved
decision creates no `Entity`, stores no operational alias and merges no
candidates by itself. The decision table is a provenance-bearing review ledger
that the entity-resolution stage must consume explicitly.

---

### Candidate Resolution Decision

A `CandidateResolutionDecision` is a separate append-only human decision about
one exact `EntityCandidate`. It does not masquerade as an alias judgment and
does not require a second form. Its status is `assigned`, `not_entity` or
`revoked`; its
explicit scope is either:

- `single`, covering only the seed candidate; or
- `exact_canonical`, covering all currently persisted candidates with the same
  normalized entity type and exact candidate-canonical text.

The broader scope is an operator decision, not an inferred alias. It may group
separate observations of canonical `un` as one organization, but it never
expands `un` to `united nations`; that relationship remains the responsibility
of the alias-review workflow.

Each revision records a non-blank reason and reviewer. Revisions form an
append-only supersession chain for the seed candidate, and the scope cannot
change inside that chain. An `assigned` revision either creates a new `Entity`
from the seed candidate or links the selected candidates to one explicitly
named existing entity. Every candidate's complete
`text → ENTITY_MENTIONS → ENTITY_CANDIDATES` provenance is checked before any
decision or assignment is persisted.

`CandidateResolutionEvidence` records the exact assigned revision applied to
the entity. A later `revoked` revision adds no new application evidence, so
registry validity becomes `revoked` without deleting the entity, its
assignments, the earlier decision or its evidence. Moving an already assigned
candidate to another entity and merging two entities remain separate future
workflows and fail closed here.

A `not_entity` revision is an explicit rejection of the NER observation, not
an identity with a special name. It creates neither an `Entity` nor an
assignment. `CandidateResolutionExclusion` freezes every candidate actually
reviewed by that revision, including the exact matched set selected by
`exact_canonical`; candidates added later do not inherit the old decision.
The applied exclusion retains its reviewer, reason, scope and complete
candidate provenance. A later `revoked` revision makes those observations
unassigned again without deleting the exclusion evidence. An assigned
candidate and an active not-entity decision may never cover the same
observation.

---

### Entity Registry

The entity registry consumes either one exact latest approved `AliasDecision`
or one explicit `CandidateResolutionDecision`. It never treats a heuristic
score, string equality alone or an earlier superseded decision as
authorization.

Creating an `Entity` requires the operator to select one of the proposal's two
candidate forms as the canonical name. The registry stores:

- the persistent entity type and canonical name;
- the exact `EntityCandidate` supplying that name;
- exactly one creation decision: alias approval or candidate resolution;
- one unique assignment for every candidate observation attached to it;
- every approved decision subsequently used as resolution evidence.

If one proposal candidate already belongs to an entity, a later approved
proposal may extend that entity with the unassigned candidate. If both
candidates already belong to different entities, resolution stops: joining
two established identities requires a separate future merge workflow with its
own history.

Registration is idempotent for the same approval and does not rewrite
candidate, proposal or decision rows. A later review decision may supersede an
approval, but it does not silently delete historical registry records. Before
the registry is used as analytical truth, the read-only registry audit derives
validity from the latest decision for every proposal represented in an
entity's evidence or candidate assignments.

The derived states are `active`, `pending_reapplication`, `needs_review` and
`revoked`. An alias link is active only when its exact latest approval is
recorded as `EntityResolutionEvidence`; a direct candidate link is active only
when its exact latest assigned revision is recorded as
`CandidateResolutionEvidence`. A newer review or revocation blocks the entity.
These are computed states rather than mutable columns, so historical entities,
assignments, evidence and decision chains remain intact.

The first downstream safety rule is deliberately conservative: an entity is
safe only when all of its recorded proposal links are active. This rule does
not claim that a revoked edge proves every candidate assignment false; it
prevents analytical consumers from treating a partly invalidated identity as
settled before a later graph-aware repair or split workflow exists.

The safe entity projection is the operational read boundary for that rule. It
returns an entity atomically or omits it atomically; candidate assignments from
a blocked entity are never exposed as a partial resolved identity. A projected
entity includes:

- its persistent identifier, type and canonical candidate;
- all assigned candidate observations;
- each candidate's document-version, derived-artifact and mention provenance;
- the exact alias or candidate-resolution decision that originally assigned
  each candidate;
- every currently active alias and direct-candidate resolution revision.

The projection is derived at read time and stores no second `safe` flag. Audit
reporting and downstream selection share one validity evaluator, preventing a
consumer-facing query from drifting away from the documented review semantics.
Entities with missing reconstructable evidence are unsafe by default.

The document-centric entity projection applies this same computed boundary to
one immutable `DocumentVersion`. It exposes only safe entities that have
assigned candidate observations in that exact version. Each projected
occurrence preserves:

- the exact `EntityCandidate`, `EntityMention` and derived artifact;
- surface, normalized and candidate-canonical text;
- source label and start-inclusive/end-exclusive character offsets;
- the exact decision that assigned the candidate;
- the active approval revisions supporting the containing entity.

The entity remains the unit of safety: one invalid registry link suppresses
all of that entity's occurrences in every document projection. The
document-version filter does not weaken or locally reinterpret registry
validity. Conversely, candidate observations assigned to the entity in other
versions are not returned as if they occurred in the requested document.

This projection is derived at read time, ordered deterministically and stores
no document/entity join table. Missing document versions and inconsistent
candidate, mention, artifact, version or type provenance fail explicitly.

The document entity coverage audit is the negative-space companion to that
projection. It reads all `EntityCandidate` rows for one exact
`DocumentVersion` and assigns each candidate one mutually exclusive state:
`safe_resolved`, `not_entity`, `unassigned`, `blocked`, or
`invalid_provenance`.

`safe_resolved` uses the same complete registry validity snapshot as the safe
entity projections. `blocked` means that a candidate assignment exists but the
entity is unsafe as a whole; the row retains the blocking validity states.
`not_entity` means an active, provenance-valid human decision rejected the
observation as a false-positive entity candidate. `unassigned` means neither
a persistent identity nor an active not-entity decision has consumed the
candidate.
`invalid_provenance` exposes a mismatch or missing link among the candidate,
mention, assignment and entity rather than silently dropping it.

Coverage counts are computed after the optional entity-type filter and before
the output limit. They are therefore suitable for declaring analytical
limitations: absence from the positive projection can be distinguished from
absence of extracted candidates. The audit is read-only and introduces no
stored coverage flag or migration.

---

### Statement

A communicative act attributed to a speaker.

Examples:

- a politician's quotation;
- an official announcement;
- an editorial assertion;
- a spokesperson's response.

Important properties:

- speaker;
- audience;
- venue;
- date;
- quotation or paraphrase;
- surrounding context;
- source document.

A statement may contain one or more claims.

---

### Claim

The smallest independently assessable assertion extracted from a document or statement.

Examples:

- "The agreement was signed on 14 March."
- "Unemployment fell by two percentage points."
- "Country X initiated the attack."

Important properties:

- subject;
- predicate;
- object or value;
- time;
- location;
- modality;
- certainty expressed by the speaker;
- source attribution;
- original text span.

A claim must remain separate from its verification status.

---

### Evidence

Information relevant to evaluating a claim or inference.

Evidence may:

- support;
- contradict;
- contextualize;
- weaken;
- leave unresolved.

Important properties:

- evidence type;
- source;
- referenced claim or inference;
- relationship type;
- strength;
- independence from other evidence;
- exact citation;
- temporal relevance.

A government statement is strong evidence that the government made that statement. It is not automatically strong evidence that the statement's content is true.

---

### Entity

A persistent identifiable object.

Examples:

- person;
- organization;
- country;
- location;
- company;
- political party;
- institution;
- document;
- physical object.

Important properties:

- canonical name;
- aliases;
- entity type;
- active period;
- external identifiers;
- merge history.

Entity resolution must preserve uncertainty when two references may or may not identify the same object.
The initial registry implements explicit creation, evidence-backed candidate
assignment and append-only revocation. Active periods, external identifiers,
canonical-name revision, reassignment, entity splitting and entity merge
history remain later stages.

---

### Event

A time-bounded real-world occurrence or process reconstructed from claims, observations and evidence.

Examples:

- election;
- military strike;
- treaty signing;
- protest;
- policy change;
- market crash;
- diplomatic meeting.

Important properties:

- event type;
- start and end time;
- location;
- participants;
- related claims;
- supporting evidence;
- status;
- confidence;
- alternative interpretations.

An event is not a single article. It is a structured cluster that may evolve as new information arrives.

Possible lifecycle states:

- detected;
- emerging;
- active;
- stabilized;
- historical;
- disputed.

---

### Frame

A recurring way of presenting an event or issue.

A frame determines:

- who is presented as the actor;
- who is presented as the victim;
- which causes are emphasized;
- which consequences are emphasized;
- which information is omitted;
- which moral evaluation is encouraged.

Examples:

- self-defence frame;
- humanitarian-crisis frame;
- external-threat frame;
- economic-necessity frame;
- historical-justice frame.

Frames belong to the Information Layer and must reference observable textual evidence.

---

### Narrative

A recurring explanatory pattern linking actors, events, causes and moral judgments across multiple documents.

A narrative is not a single phrase. It is a pattern detected across time and sources.

Important properties:

- constituent claims;
- frames;
- associated entities and events;
- participating sources;
- first and last observation;
- temporal prevalence;
- geographic distribution;
- competing narratives.

---

### Analysis Result

A versioned output produced by an analytical method.

Examples:

- linguistic metric;
- detected rhetorical technique;
- factual consistency assessment;
- event-cluster assignment;
- narrative similarity score;
- rhetorical-shift measurement.

Important properties:

- analysis type;
- target object;
- method name;
- method version;
- analysis-run identifier;
- model or lexicon version where applicable;
- configuration;
- input object versions;
- software version;
- result;
- confidence;
- evidence chain;
- creation time;
- warnings and execution limitations.

Analysis results must be reproducible where the underlying method is deterministic.

---

### Inference

A conclusion or hypothesis derived from observations, claims, events or other analysis results.

Examples:

- event A may have contributed to rhetoric shift B;
- actor X may receive an economic benefit from event Y;
- several sources may be reproducing the same narrative;
- a statement may be intended to mobilize an audience.

Important properties:

- inference type;
- proposition;
- supporting evidence;
- contradicting evidence;
- required assumptions;
- alternative explanations;
- confidence or evidence strength;
- method version.

An inference must never be stored without an explicit evidence chain.

---

### Potential Effect

A plausible consequence of an event.

Examples:

- oil prices increase;
- public approval changes;
- military spending rises;
- trade routes become less reliable.

Potential effects may be:

- observed;
- projected;
- disputed;
- unsupported.

Argus must distinguish observed effects from hypothetical effects.

---

### Potential Beneficiary

An actor that may benefit from an event or one of its effects.

A potential beneficiary record does not imply responsibility for causing the event.

Required structure:

- actor;
- event or effect;
- benefit type;
- benefit mechanism;
- supporting evidence;
- counterevidence;
- required assumptions;
- alternative explanations;
- evidence strength.

Example:

- Actor: oil-exporting country;
- Effect: increase in oil price;
- Mechanism: higher export revenue;
- Evidence: price and export-volume data;
- Limitation: political or military costs may exceed economic gains.

---

## Core Relationships

### Source and Document

```text
Source
    publishes
        Document
```

### Document and Observation

```text
Document
    contains
        Observation
```

### Document, Statement and Claim

```text
Document
    contains
        Statement

Statement
    expresses
        Claim
```

### Claim and Event

```text
Claim
    refers_to
        Event
```

### Claim and Evidence

```text
Evidence
    supports
        Claim

Evidence
    contradicts
        Claim

Evidence
    contextualizes
        Claim
```

### Documents and Frames

```text
Document
    uses
        Frame
```

### Claims and Narratives

```text
Claim
    contributes_to
        Narrative
```

### Events

```text
Event
    precedes
        Event

Event
    overlaps
        Event

Event
    may_influence
        Event

Event
    has_effect
        Potential Effect
```

The `may_influence` relationship is analytical and must include evidence and confidence. It must not be treated as proven causation.

### Inferences

```text
Inference
    based_on
        Observation

Inference
    based_on
        Claim

Inference
    based_on
        Event

Inference
    supported_by
        Evidence

Inference
    contradicted_by
        Evidence
```

### Potential Beneficiaries

```text
Potential Beneficiary
    may_benefit_from
        Potential Effect
```

---

## Evidence Chain

Every non-trivial conclusion must be traceable through an evidence chain.

Example:

```text
Potential-beneficiary hypothesis
    ↓
Observed increase in oil prices
    ↓
Economic dataset
    ↓
Specific table and timestamp
```

Example:

```text
Appeal-to-fear detection
    ↓
Rhetorical feature
    ↓
Sentence spans
    ↓
Original document
    ↓
Source and publication date
```

The user must be able to inspect every step.

---

## Separation of Fact and Interpretation

Argus must distinguish:

- collected content;
- direct observations;
- attributed claims;
- supported facts;
- disputed reconstructions;
- analytical measurements;
- hypotheses;
- speculative alternatives.

These categories must not be collapsed into a single generic `fact` type.

---

## Versioning

All extracted and derived objects must record:

- creation time;
- method or model identifier;
- method, model or lexicon version;
- analysis-run identifier where applicable;
- relevant configuration;
- input object and source-data versions;
- input content hash where applicable;
- software version;
- superseded or current status.

Historical analysis results should remain available after methods are updated.

---

## Uncertainty

Uncertainty is a first-class property.

Argus must be able to represent:

- unknown;
- disputed;
- partially supported;
- contradictory;
- time-sensitive;
- source-dependent;
- method-dependent.

A missing answer is preferable to unsupported certainty.

### Document entity readiness

Entity-dependent downstream analysis must cross an explicit readiness
boundary for the exact immutable `DocumentVersion`. Readiness is a derived
read-only contract over complete candidate coverage; it is not persisted as a
second source of truth.

```text
DocumentVersion
    entity readiness
        ready
        no_candidates
        incomplete
        blocked
        invalid
```

`ready` requires one or more candidates and exact 100% reviewed coverage:
every candidate is either `safe_resolved` or `not_entity`, with no overlap.
The remaining states are not percentages or confidence bands:
they are explicit reasons that downstream consumption is unsafe. Provenance
damage outranks a blocked registry identity, which outranks an unassigned
candidate. A type-filtered contract applies only to that entity type and must
carry the filter in its detached result.

### Corpus entity readiness

`CorpusEntityReadinessReport` is a detached, read-only inventory of the same
document readiness contract across all persisted document versions. It
contains complete document and candidate totals, counts for every readiness
state and a bounded ordered set of document-level reports.

The corpus view does not persist a second readiness state and does not weaken
the document contract. Registry validity is evaluated once for the batch.
Versions without candidates remain visible as `no_candidates`. A status filter
and display limit affect only detailed rows, never complete corpus counts.

### Candidate resolution queue

`CandidateResolutionQueue` is a detached, read-only operator view over one
document's current readiness state. It contains document identity and metadata,
the complete readiness counts and a bounded set of unresolved canonical
groups. The full number of unresolved groups remains available even when the
display is limited.

Each `CandidateResolutionQueueGroup` is keyed by `EntityType` and exact
canonical text. It carries an unassigned seed candidate, occurrence counts in
the document and corpus, document surface variants, bounded source contexts
and all entity identifiers already assigned inside that exact-canonical
corpus scope. Its scope state has only three structural values:

- `new_entity`: no candidate in the scope is assigned;
- `extends_entity`: assigned candidates point to one entity;
- `invalid_provenance`: at least one candidate in the corpus scope has an
  invalid immutable provenance chain;
- `conflict`: assigned candidates point to multiple entities, or the scope
  overlaps an active not-entity decision.

The DTO does not assert that two different canonical forms are aliases and
does not persist queue position. An automatically selected document minimizes
remaining unassigned observations after first preferring versions without
blocked or invalid evidence. Explicit candidate decisions remain the only
write boundary.

### Ready document selection

`ReadyDocumentSelection` is the narrow admission DTO for entity-dependent
downstream work. Its items contain only immutable document-version identifiers
and exact safe candidate counts taken from `ready` corpus reports. Unsafe
states have no representation in `ReadyDocumentVersion`.

The selector preserves the complete ready-document count while bounding the
returned items. It verifies the readiness flag, status, type boundary and
candidate counts again when constructing each item, so an inconsistent
upstream report is rejected rather than partially consumed. No selection is
persisted; the entity registry and candidate provenance remain the source of
truth.

### Document analysis input bundle

`DocumentAnalysisInputBundle` is the detached, immutable handoff to
entity-dependent analytical code. It contains:

- stable document identity and exact version metadata;
- raw-artifact identifier and digest;
- the one text artifact from which all selected mentions and candidates were
  derived, including its method, version, schema, digest and quality limits;
- the strict readiness report;
- the complete safe document entity projection;
- every active not-entity observation with its candidate, mention, span,
  exact decision, revision, scope, reason and reviewer.

The bundle is constructed inside one caller-owned database snapshot. Registry
validity is evaluated once and reused for coverage and projection. The
projection occurrence count must equal the readiness `safe_resolved` count,
the not-entity input count must equal readiness `not_entity`, and both views
must be unbounded inside the bundle.

Candidate and mention rows do not share one derived-artifact identifier.
Their valid provenance is the explicit three-stage chain
`text -> ENTITY_MENTIONS -> ENTITY_CANDIDATES`; each output artifact records
its exact input artifact and content hash. Missing artifacts, wrong types,
cross-version links, hash conflicts, mismatched text spans or more than one
text input make the bundle unavailable. This contract corrects and replaces
the earlier simplified assumption that candidate and mention projections
should carry the same artifact identifier.

The bundle is a read model only. It introduces no table, cache, readiness flag
or mutable analysis state.

### Analysis run

`AnalysisRun` is the immutable input contract and mutable execution ledger for
one analytical execution. It is created only from a strict
`DocumentAnalysisInputBundle` inside the same database transaction. Its
lifecycle is `prepared -> running -> completed` or
`prepared/failed -> running -> failed`; retrying a failure is always explicit.

Each row stores:

- the exact `DocumentVersion` and entity-type scope;
- analytical method and method version;
- automatically verified Argus software version;
- canonical JSON configuration and its SHA-256 digest;
- input-manifest schema version;
- canonical input manifest and its SHA-256 fingerprint;
- status, attempt count, last error and execution timestamps;
- creation time.

The `document-analysis-input@2` manifest includes every identity needed to
reconstruct and verify
the bundle: raw and text artifacts, their digests, readiness counts, resolved
entities, exact occurrences, assignment decisions and latest active resolution
revisions, plus every active not-entity decision and its exact reviewed
observation. Full text is not duplicated; its immutable artifact identifier,
digest and character count anchor the content.

The reproducible key is the combination of input fingerprint, analytical
method, method version, software version and configuration digest. Repeating
the same preparation is idempotent. A configuration change creates a distinct
run even with the same input; a registry or provenance change creates a
distinct input fingerprint. Preparation accepts only an exact method and
method-version pair present in the executable method registry. This prevents
unexecutable names from being recorded as if an implementation existed.

Software identity is not accepted from the caller. A clean Git checkout stores
`git:<full-commit-sha>` and refuses preparation when tracked, staged or
untracked files differ from the commit. An unpacked distribution without
`.git` stores `source-sha256:<source-tree-digest>`, calculated over the actual
Argus package, migrations, entrypoint, dependency lock and Alembic
configuration. Existing but unusable Git metadata is an error rather than a
reason to downgrade to content-hash provenance. Both forms are self-describing
and fit the existing `software_version` field and reproducible key, so this
contract requires no schema migration.

### Analysis result

`AnalysisResult` is the single immutable output of one completed
`AnalysisRun`. It stores:

- the exact run identifier;
- a result-schema version;
- a canonical JSON payload;
- explicit warnings;
- a nullable evidence-set hash (`NULL` only for results created before the
  external evidence contract);
- a SHA-256 hash over schema, payload, warnings and, for current results, the
  evidence-set hash;
- creation time.

The one-result-per-run constraint prevents an execution from silently
overwriting history. A successful repeated execution request returns the
existing hash-verified result without invoking the method again. A corrupted
run manifest, configuration, text artifact, evidence row, evidence-set hash or
result hash fails closed.

### Analysis evidence

`AnalysisEvidence` is one immutable observation produced by an exact method
version and owned by one `AnalysisResult`. It stores a stable zero-based order,
evidence schema, category, modality, canonical source locator, method-specific
payload and a SHA-256 hash over those fields. The ordered list of row hashes is
itself content-addressed by the result's evidence-set hash. Result, evidence
rows and the completed lifecycle transition are committed atomically.

The current executable locator is `text_span`: derived-text artifact identity,
half-open `start_char`/`end_char` coordinates and a SHA-256 hash of the exact
UTF-8 excerpt. Read paths verify the artifact against the run manifest, slice
the stored source text again and compare both excerpt and digest. Database
modality values reserve `image`, `audio` and `video`, but execution rejects
them until media input manifests and modality-specific locator validators
exist; a JSON shape alone is not treated as proof that media evidence points
to source bytes.

The former `analysis_evidence` table belongs to the legacy article discourse
pipeline and is preserved, without rewriting its rows, as
`discourse_analysis_evidence`.

One registered method is
`lexical-discourse@lexical-en-v0.2`. It adapts the existing deterministic
English lexical discourse analyzer, emits metrics in
`lexical-discourse-result@2`, and writes every matched sentence separately as
`lexical-discourse-evidence@1`. It accepts an empty configuration only. Other
languages and method configurations require their own explicit versions.
Historical `lexical-en-v0.1` results remain readable with their original
embedded evidence and hashes; coordinates are not fabricated retroactively.

The first registered synthetic-origin method is
`synthetic-origin-text@structural-en-v0.1`. Its
`synthetic-origin-text-result@1` payload separates an uncalibrated
`detector_score` from the reserved `synthetic_probability`, records
eligibility, structural metrics and limitations, and always concludes
`inconclusive`. It requires at least 250 words and 10 sentences before
producing a score. Formulaic-language matches are weak local observations,
stored as `synthetic-origin-text-evidence@1`; they are never represented as
proof of synthetic authorship. The allowed conclusion vocabulary intentionally
has no `verified_human` value.

Calibration artifacts are intentionally outside operational `AnalysisRun`
storage. A `synthetic-origin-calibration-sample@1` JSONL corpus binds exact
text, label, provenance, language, genre, source group and immutable split.
Canonical hashes identify every sample, split and corpus. A
`synthetic-origin-threshold@1` decision binds a threshold to one method version
and calibration split; a `synthetic-origin-evaluation@1` report binds held-out
metrics to the same corpus and untouched test split. This prevents benchmark
experiments from being mistaken for analysis of collected documents.

Corpus construction uses two additional file contracts.
`synthetic-origin-source-record@1` binds one label and its required provenance
to the SHA-256 of one immutable UTF-8 source file.
`synthetic-origin-corpus-build@1` records the canonical manifest hash, source
content hashes, normalization and deduplication policy versions, salted
group-split policy, resulting corpus and split hashes, and a self-verifying
receipt hash. These are offline artifacts rather than database entities.

The intake layer adds one preserved file contract,
`synthetic-origin-generation-log@1`. It binds a synthetic sample to the exact
prompt hash, output hash, provider, model, model snapshot, generation time and
finite JSON parameters, and carries a canonical self-hash. Intake sidecars use
the existing `synthetic-origin-source-record@1` contract; they are not a
competing manifest schema or mutable database record.

### Analysis execution attempt

`AnalysisExecutionAttempt` is the append-only audit record for one claimed
execution of an `AnalysisRun`. It stores the run and monotonically increasing
attempt number, status, start and finish times, bounded error, and—only for an
explicit stale recovery—the operator and reason. Completed, failed and
abandoned attempts are never overwritten by a later retry.

Migration from the earlier run-only lifecycle reconstructs only the latest
observable attempt for runs whose `attempt_count` is non-zero and marks that
row as migrated. It does not invent missing detail for earlier retries.
