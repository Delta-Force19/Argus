# Argus

Argus is an open-source, explainable information-space analysis platform.
It collects attributable documents, preserves their provenance, extracts
versioned observations, and provides reproducible evidence for comparing how
sources describe the world.

Argus is not a truth oracle, a news aggregator, or a general-purpose chatbot.
It does not treat a source's reputation as proof and does not present
uncalibrated scores or hypotheses as facts.

## Project status

Argus is under active development. The current codebase includes:

- RSS collection and normalized source/endpoint storage;
- immutable retrieval, raw-artifact, document, and document-version records;
- provenance-anchored transcript intake for plain text, WebVTT, and SubRip;
- main-text extraction and legacy English discourse analysis;
- versioned entity mentions, candidate generation, alias proposals, and
  reviewed entity resolution;
- fail-closed document readiness and atomic analysis-input bundles;
- reproducible analysis runs with attempt, software, result, and exact text
  evidence provenance;
- an experimental structural synthetic-origin baseline and offline
  calibration-corpus tooling;
- explainable pairwise document similarity signals for future event
  clustering. The score is not a same-event decision.

Event reconstruction, claim extraction, fact verification, narrative
detection, and calibrated event clustering are not implemented yet. Their
current boundaries are documented under `docs/analysis/`.

## Principles

- Evidence before conclusions.
- Measurements before interpretations.
- Explicit uncertainty and limitations.
- Immutable, attributable inputs.
- Versioned methods and reproducible outputs.
- Deterministic algorithms where they are sufficient.
- Human review at identity and interpretation boundaries.

## Quick start

Argus currently targets Python 3.12 and SQLite.

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m alembic upgrade head
python main.py --help
python -m pytest -q
```

On POSIX shells:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m alembic upgrade head
python main.py --help
python -m pytest -q
```

The CLI also upgrades the configured database to the current Alembic head
before executing application commands. Back up persistent databases before
manual migration work.

## Common commands

```text
python main.py collect
python main.py parse --newest --limit 20
python main.py analyze --limit 20
python main.py latest-news --limit 10
python main.py ready-document-versions --limit 10
python main.py compare-document-event-similarity --help
python main.py ingest-transcript --help
python main.py inspect-document-text --document-version-id 34
python main.py segment-event-fragments --document-version-id 34
```

Use `python main.py --help` as the authoritative command inventory; analytical
and review commands intentionally expose exact identifiers and provenance.

## Repository map

- `argus/collector/` — legacy RSS collection boundary;
- `argus/acquisition/` — normalized discovery and retrieval contracts;
- `argus/parsers/` — text extraction;
- `argus/analysis/` — deterministic analytical methods and corpus tooling;
- `argus/services/` — application workflows;
- `argus/storage/` — repositories and artifact storage;
- `argus/interface/` — CLI and Telegram reader interface;
- `migrations/` — Alembic schema history;
- `tests/` — unit, service, CLI, migration, and regression tests;
- `docs/` — architecture, methodology, operation, and future boundaries.

See [Architecture Overview](docs/architecture/overview.md),
[Platform Scope](docs/architecture/platform_scope.md),
[Data Model](docs/architecture/data_model.md), and
[Development Roadmap](docs/development/roadmap.md).

## Data and secrets

Runtime databases, logs, raw artifacts, calibration sources, and local secrets
must remain outside version control. Telegram credentials are supplied through
environment variables described in [Configuration](docs/configuration.md).

## Contributing

Changes should be incremental, tested, documented, and migration-safe.
Analytical outputs must preserve enough evidence and method provenance to be
independently inspected. See [Coding Style](docs/development/coding_style.md).

No project license has been selected yet. Until a license file is added, the
repository is source-visible but no open-source reuse terms are granted.
