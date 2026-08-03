# Coding Style

## General rules

- Target the Python version documented by the project setup.
- Prefer explicit types, immutable result dataclasses, and small functions.
- Keep imports free of filesystem, network, and database side effects.
- Use services for workflows and repositories for persistence.
- Make transaction ownership explicit; repositories do not commit implicitly
  unless an existing documented legacy contract requires it.
- Represent stages, statuses, document types, and other stored vocabularies
  with shared enums rather than ad-hoc strings.

## Analytical code

- Preserve exact inputs, method versions, schemas, and quality limitations.
- Decompose scores into inspectable signals.
- Treat missing data explicitly; do not silently convert unavailable evidence
  to a negative observation.
- Do not call a heuristic a probability without calibration.
- Keep facts, observations, claims, decisions, and hypotheses distinct.

## Changes

- Prefer incremental changes over broad rewrites.
- Add or update tests for behaviour, error paths, and migrations.
- Update architecture or methodology documentation in the same increment.
- Run `python -m pytest -q`, `python -m compileall -q argus tests`,
  `python -m alembic heads`, and `git diff --check` before committing.
- Never edit a shared Alembic revision; add a new revision.
- Keep runtime data, secrets, logs, artifacts, and calibration sources out of
  version control.

Formatting changes should remain scoped to touched code until a repository-wide
formatter and linter configuration is adopted.
