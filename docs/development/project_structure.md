# Project Structure

```text
Argus/
├── argus/
│   ├── acquisition/   normalized discovery and retrieval contracts
│   ├── analysis/      deterministic analysis and corpus tooling
│   ├── collector/     legacy RSS collection
│   ├── interface/     CLI and Telegram reader
│   ├── parsers/       text extraction
│   ├── services/      application workflows
│   └── storage/       repositories and artifact storage
├── data/              runtime data; created locally and ignored
├── docs/              architecture, methodology, and operations
├── migrations/        Alembic environment and revision history
├── tests/             automated regression suite
├── alembic.ini        migration configuration
├── main.py            CLI entry point
└── requirements.txt   pinned runtime and test environment
```

Package `__init__.py` files may intentionally remain empty. Empty feature
modules, documentation pages, and test files are not accepted as roadmap
markers: planned work belongs in documentation or the issue tracker.

The current repository uses a flat application layout and a centralized
`argus/models.py`. Structural refactoring must preserve migration imports,
runtime entry points, and the historical schema contract.
