# Asyncopation

> **Status: pre-alpha / design phase.** Nothing here is usable yet. See the [plan](docs/design/plan.md).

Truly asynchronous execution for the Django ORM. Django still builds the SQL; Asyncopation
executes it on native async drivers (psycopg3 for PostgreSQL, asyncmy for MySQL/MariaDB)
instead of hopping to a thread with `sync_to_async`.

| | |
|---|---|
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| Requires | Python 3.12+, Django 6.1, ASGI |
| Name | *syncopation* (an off-beat rhythm) + *async*; a nod to Django Reinhardt |

```python
books = await Book.aobjects.filter(published=True).alist()  # native async I/O
await books[0].afetch("author")  # loads authors for all peers in one query
```

Translations: [Korean](README-KO.md)

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Docker. MySQL differential tests also need the
`mysqlclient` build dependencies (`brew install mysql-client pkg-config` or
`apt install libmysqlclient-dev`) and `uv sync --group mysql-sync`.

```bash
uv sync                     # install dev + e2e dependency groups
make db-up                  # PostgreSQL, MySQL, MariaDB via docker compose (tmpfs)
make check                  # ruff + ty + import-linter + guards + agent config sync + unit tests
make test-int               # integration tests (ASYNCOPATION_DB=postgresql|mysql|mariadb)
make test-e2e               # boots uvicorn on the harness site
make bench-micro            # in-process micro-benchmarks
make bench                  # k6 load test: WSGI vs ASGI core vs Asyncopation
make help                   # everything else
```

Tooling: uv, pytest (+ pytest-asyncio, pytest-django, Hypothesis), ruff (format + lint),
ty (types), import-linter (layers), project guards in `tools/guards`, k6 (load).

## Working with coding agents

The repository is set up for Claude Code, Codex and Pi as first-class citizens. Start with
[docs/workflow.md](docs/workflow.md); agents read [AGENTS.md](AGENTS.md) (Claude Code via
[CLAUDE.md](CLAUDE.md)).

## Documentation

- [docs/design/plan.md](docs/design/plan.md) - background, design, roadmap
- [docs/architecture.md](docs/architecture.md) - layers and invariants
- [docs/adr/](docs/adr/) - architecture decision records
- [docs/workflow.md](docs/workflow.md) - the agentic development workflow
- English is the source language; translated documents use a `-<LANG>` suffix (`README-KO.md`),
  see [docs/i18n.toml](docs/i18n.toml).
