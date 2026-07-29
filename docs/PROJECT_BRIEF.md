# Argus — Project Brief

Version: v0.1.1
Status: Active Development

---

# Vision

Argus is an open-source platform for objective, transparent and reproducible analysis of the global information space.

The project is **not** intended to predict the future, determine "absolute truth", or replace human judgement.

Its purpose is to collect information from multiple independent sources, measure observable characteristics of documents, compare narratives, reconstruct information flows and provide explainable analytical reports.

Every conclusion produced by Argus must be reproducible and supported by evidence.

---

# Core Principles

## Evidence First

Every metric must be explainable.

Every statement must have supporting evidence.

No "black box" conclusions.

---

## Transparency

Every processing step is stored.

Every algorithm has a version.

Every result can be reproduced.

---

## Objectivity

Argus does not classify countries, organizations or people as "good" or "bad".

It measures observable properties of texts.

Interpretation belongs to the user.

---

## Modularity

Every processing stage is independent.

Every module should be replaceable without rewriting the whole system.

---

# Long-Term Goal

Build an analysis engine capable of understanding how information spreads through media ecosystems.

Not only:

- what happened

but also

- who said it
- how it was described
- how rhetoric changed
- which narratives emerged
- which claims contradict historical facts
- which actors may benefit from different interpretations

The last point must always be presented as probabilistic hypotheses rather than factual conclusions.

---

# Current Scope

Current implementation works only with news articles.

Architecture, however, is designed around a future generalized Document model.

Future document types:

- News
- Government reports
- Research papers
- PDF documents
- YouTube transcripts
- Telegram posts
- Social media
- Interviews
- Speeches

---

# Current Pipeline

RSS

↓

Collection

↓

Metadata Storage

↓

Parsing

↓

Raw Text

↓

Discourse Analysis

↓

Database

---

# Current Modules

collector/
RSS collection

parsers/
HTML extraction

storage/
Repositories

analysis/
Discourse analysis

services/
Business logic

interface/
CLI

database/
SQLAlchemy

logging/
Structured logging

tests/
Validation Suite

docs/
Architecture

---

# Current Infrastructure

Implemented:

- SQLite
- SQLAlchemy 2
- CLI
- Logging
- Processing State
- Retry system
- Versioned analysis
- Unit tests
- GitHub

---

# Processing Pipeline

collect

↓

parse

↓

analyze

↓

future modules...

---

# Processing State

Every processing stage stores

- pending
- running
- done
- failed

with

- attempts
- timestamps
- errors

Future stages will reuse the same model.

---

# Development Philosophy

Never add functionality before architecture.

Prefer simple and explainable solutions.

Avoid AI when deterministic algorithms provide better transparency.

Every major decision should improve future extensibility.

---

# Roadmap

v0.1.x

Infrastructure

Logging

Testing

Architecture cleanup

Configuration

---

v0.2

Knowledge Extraction

Entity Recognition

Claim Extraction

---

v0.3

Event Detection

Narrative Detection

Cross-source comparison

---

v0.4

Knowledge Graph

Temporal reasoning

Fact verification

---

Future

Explainable geopolitical analysis

Cross-country media comparison

Narrative evolution

Historical consistency checking

Probabilistic hypothesis generation

---

# Coding Standards

Readable code over clever code.

Small independent modules.

No hidden side effects.

Dependency injection where appropriate.

Version every analytical algorithm.

Document every architectural decision.

---

# Important Rule

Argus is an analytical platform.

It must never present speculation as fact.

Whenever uncertainty exists,
Argus should communicate uncertainty explicitly.

Evidence > Confidence > Interpretation.

Never the opposite.