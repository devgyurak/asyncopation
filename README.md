# Asyncopation

> **Status: pre-alpha.** The PostgreSQL lifecycle (`INSTALLED_APPS`, `setup()` / `close()`, `LifespanApp`, the driver registry) and `asyncopation.testing` are usable; the query API shown below is the design target, not yet implemented. See the [plan](docs/design/plan.md).

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

## Setup (PostgreSQL)

Add the app, so that `AppConfig.ready()` configures the async drivers from `DATABASES`, and wrap
the ASGI application so that the pools are opened at startup and closed at shutdown:

```python
# settings.py
INSTALLED_APPS = ["asyncopation", ...]

# asgi.py
from asyncopation.connection.asgi import LifespanApp
from django.core.asgi import get_asgi_application

application = LifespanApp(get_asgi_application())
```

Scripts and tests call `await asyncopation.setup()` and `await asyncopation.close(timeout=10)`
themselves. Using a database before `setup()` (the future query path) opens its pool once, lazily, with a logged
warning that recommends the lifespan wrapper. `setup()` opens every database whose engine is
supported and skips the others; pass `LifespanApp(app, aliases=[...])` or `setup(aliases=[...])` to
choose. MySQL/MariaDB are not supported yet.

**In tests**, a test runner creates the test databases after `ready()` and renames `NAME`. Call
`asyncopation.configure()` once they exist (for example in a session fixture right after
`setup_databases`); until then opening a driver fails with `ImproperlyConfigured` instead of
connecting to the original database.

## Testing upper layers without a database

`asyncopation.testing.RecordingDriver` implements the `AsyncDriver` protocol in memory: queue the
results a test needs, run the code under test, then inspect the statement log. `Capabilities` and
`ExecResult` are re-exported from `asyncopation.testing`.

```python
import asyncio

from asyncopation.testing import ExecResult, RecordingDriver


async def main() -> None:
    driver = RecordingDriver()
    await driver.open()
    driver.queue([(1, "a"), (2, "b")], ExecResult(rowcount=1))  # FIFO, one per statement
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT id, name FROM t WHERE id > %s", [0])
        await conn.execute("DELETE FROM t WHERE id = %s", [1])
    assert rows == [(1, "a"), (2, "b")]
    assert [s.kind for s in driver.statements] == ["fetch_all", "execute"]
    await driver.close()


asyncio.run(main())
```

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
