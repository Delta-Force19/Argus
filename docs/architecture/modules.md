# Module Boundaries

## Dependency direction

Argus separates transport, application workflows, persistence, and analytical
methods. New code should depend on narrow contracts rather than importing CLI
or runtime setup into lower layers.

## Current packages

- `argus/acquisition/` defines normalized connector and retrieval contracts.
- `argus/collector/` contains the legacy RSS collection adapter.
- `argus/parsers/` extracts document text.
- `argus/analysis/` contains deterministic analytical functions and offline
  corpus tooling.
- `argus/services/` coordinates application workflows and transaction
  boundaries.
- `argus/storage/` owns repositories and content-addressed artifact storage.
- `argus/interface/` exposes CLI and Telegram reader behaviours.
- `argus/intelligence/` reserves the future reasoning layer; it has no active
  implementation.
- `argus/logging/` configures application logging.

SQLAlchemy models currently remain centralized in `argus/models.py` while the
schema is evolving. Splitting them requires an architectural migration, not a
mechanical file move.

## Rules

1. CLI functions validate and render; services own workflows.
2. Repositories persist data but do not infer analytical identity.
3. Services do not create schema; Alembic owns schema evolution.
4. Analytical methods accept explicit inputs and expose limitations.
5. Runtime side effects must not occur during module import.
6. Immutable artifacts and completed analytical history are append-oriented.
