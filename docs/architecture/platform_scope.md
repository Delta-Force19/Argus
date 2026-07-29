# Platform Scope and Boundaries

## Status

Accepted for incremental implementation after v0.1.1 infrastructure cleanup.

## Purpose

Argus is an explainable information-space analysis platform. News articles are
one source of evidence, but they are not the central domain abstraction.

The platform must eventually analyze current and historical information from
media, official records, transcripts, legislation, statistical datasets,
research publications, and archival material.

Every analytical conclusion must remain traceable to its source material,
method, assumptions, uncertainty, and alternative explanations.

## Architectural principles

The analytical domain follows these principles:

1. Original source material is preserved.
2. Language and country are separate attributes.
3. Observations are separated from interpretations.
4. Claims are not automatically treated as facts.
5. Hypotheses are not presented as conclusions.
6. Analytical methods and model versions are recorded.
7. Generated summaries never replace underlying evidence.
8. Working modules are extended incrementally rather than rewritten.

## Domain model

Detailed definitions of documents, sources, observations, statements, claims,
evidence, entities, events, inferences, and analytical relationships are
maintained in the [Argus Data Model](data_model.md).

That document is the authoritative description of domain entities and their
relationships. This document defines platform-level scope, boundaries,
multilingual requirements, and the role of language models.

The existing `Article` model remains operational during migration. It will be
generalized incrementally rather than removed through a single rewrite.

## Multilingual architecture

Argus targets the six official languages of the United Nations:

- Arabic;
- Chinese;
- English;
- French;
- Russian;
- Spanish.

Language support is implemented through replaceable language-specific
pipelines sharing common analytical contracts.

English and Russian are the first implementation targets. The domain model
must support all six languages from the beginning without assuming that every
document is translated into English.

Original text is authoritative as source material. A translation is a derived
artifact and must record its method, model, version, and relationship to the
original document.

Language identifiers should support BCP 47 values, including distinctions
such as `zh-Hans` and `zh-Hant`.

## LLM boundary

A large language model may provide a conversational interface to Argus and may
help produce candidate extractions or hypotheses.

An LLM must not act as an unrecorded source of factual information.

The model should query stable application services, receive structured
evidence-backed results, and present those results at the requested level of
detail. It must not access database tables directly.

Brief responses must retain access to:

- all relevant sources;
- underlying evidence;
- analytical methods;
- uncertainty and limitations;
- timelines, graphs, and full reports.

Argus must remain operational without a generative model. External and local
models may later be supported through optional adapters.

## Incremental implementation sequence

The domain will be introduced in this order:

1. database migration infrastructure;
2. normalized source foundation;
3. protocol-independent acquisition contracts;
4. collection endpoints and retrieval provenance;
5. raw artifacts, documents, and document versions;
6. analytical provenance and result versioning;
7. multilingual processing contracts;
8. entity extraction;
9. claim extraction;
10. event reconstruction;
11. relationships and knowledge graph;
12. evidence-backed reporting;
13. optional conversational interfaces.

This sequence may evolve, but later analytical features must not bypass the
provenance and evidence foundations.