# Asyncopation

> **Status: pre-alpha.** The PostgreSQL and MySQL/MariaDB lifecycle (`INSTALLED_APPS`, `setup()` / `close()`, `LifespanApp`, the driver registry) and `asyncopation.testing` are usable, and `aq(qs)` / `aobjects` read data on PostgreSQL (`alist` / `aget` / `afirst` / `acount` / `aexists` / `async for`); the rest of the query API is the design target, not yet implemented. See the [plan](docs/design/plan.md).

Truly asynchronous execution for the Django ORM. Django still builds the SQL; Asyncopation
executes it on native async drivers (psycopg3 for PostgreSQL, asyncmy for MySQL/MariaDB)
instead of hopping to a thread with `sync_to_async`.

| | |
|---|---|
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| Requires | Python 3.12+, Django 6.1, asgiref 3.13+, ASGI |
| Name | *syncopation* (an off-beat rhythm) + *async*; a nod to Django Reinhardt |

```python
books = await Book.aobjects.filter(published=True).alist()  # native async I/O
await books[0].afetch("author")  # loads authors for all peers in one query
```

Translations: [Korean](README-KO.md)

## Setup (PostgreSQL)

Add the app, so that `AppConfig.ready()` configures the async drivers from `DATABASES`, and wrap
the ASGI application so that the pools are opened at startup and closed at shutdown
(`LifespanApp` consumes the `lifespan` scope and does not pass it on; Django's ASGI handler cannot
handle it):

```python
# settings.py
INSTALLED_APPS = ["asyncopation", ...]

# asgi.py
from asyncopation.connection.asgi import LifespanApp
from django.core.asgi import get_asgi_application

application = LifespanApp(get_asgi_application())
```

Scripts and tests call `await asyncopation.setup()` and `await asyncopation.close(timeout=10)`
themselves. Using a database before `setup()` (the query path, e.g. `alist()`) opens its pool once, lazily, with a logged
warning that recommends the lifespan wrapper. `setup()` opens every database whose engine is
supported (`django.db.backends.postgresql` and `django.db.backends.mysql`, which covers MySQL and MariaDB) and skips the others.

To combine the pools with other startup/shutdown work, call `setup()` / `close()` from your own
lifespan handler instead of wrapping the app:

```python
from contextlib import asynccontextmanager

import asyncopation


@asynccontextmanager
async def lifespan(app):  # e.g. Starlette(lifespan=lifespan)
    await asyncopation.setup()
    try:
        yield
    finally:
        await asyncopation.close()
```

**In tests**, a test runner creates the test databases after `ready()` and renames `NAME`. Call
`asyncopation.configure()` once they exist (for example in a session fixture right after
`setup_databases`); until then opening a driver fails with `ImproperlyConfigured` instead of
connecting to the original database.

## Reading data (PostgreSQL)

`aq()` wraps any QuerySet or Manager; chain it as usual and await the evaluation. Evaluation is
asynchronous end to end: Django builds the SQL, a native async driver runs it.

```python
from asyncopation import aq

books = await aq(Book.objects.filter(published=True).order_by("title")).alist()
book = await Book.aobjects.aget(pk=1)  # with `aobjects = AsyncObjects()` on the model
how_many = await Book.aobjects.filter(published=True).acount()
titles = await aq(Book.objects.values_list("title", flat=True)).alist()
```

`aq()` wraps a clone of the QuerySet you pass (the one exception is a QuerySet whose result cache is
already filled, which `alist()` only reads: a prefetched related manager, or a stored one that a user-defined method returns), so `alist()` fills the wrapper's own cache and never
yours: a QuerySet kept in a class attribute does not return stale rows to later requests. This holds for combinations too (`aq(Book.objects.none()) | qs`, `.union(qs)`, where Django returns `qs` itself): the result is a clone, and a user-defined chaining method must return a new QuerySet. A wrapper nested inside an argument (`Prefetch(queryset=aq(...))`, a list, a `Q`) is not unwrapped and is unsupported: pass its `.queryset`. Keep the wrapper on the left of `|`, `&` and `^`: `QuerySet | aq(...)` runs Django's operator, which never defers to the wrapper, and returns a plain QuerySet that does not get `FETCH_RAISE` from `aq()`. Wrapping a QuerySet whose cache is already filled (a prefetched related manager, a QuerySet evaluated synchronously) keeps that cache's instances in their original fetch mode, which is not `FETCH_RAISE`. A
Manager's own fresh QuerySet is used directly (`Manager.get_queryset()` is assumed to return a
fresh QuerySet, as Django's and related managers do). `aq()` and `aobjects` apply `FETCH_RAISE`: touching an
unloaded relation or a deferred field raises
`FieldFetchBlocked` instead of querying synchronously. Synchronous evaluators (`list(aq(qs))`,
`aq(qs).get()`, `len(...)`, `aq(qs).iterator()`, a slice with a step such as `aq(qs)[::2]`) raise
`asyncopation.SyncEvaluationError`, and `a*` methods that are not implemented yet (`acreate`,
`aiterator`, ...) raise `NotImplementedError`, as does `raw()` (it would return an unwrapped
`RawQuerySet`). A `QuerySet` that cannot match (`.none()`, `pk__in=[]`) runs no statement, but the
read API still opens the alias's pool first (ADR-0004).
The read API on PostgreSQL is `alist()`, `aget()`, `afirst()`, `acount()`, `aexists()` and `async for`
(no `prefetch_related` yet; MySQL/MariaDB follow). A model that declares `aobjects = AsyncObjects()`
(a third-party one gets it with `AsyncObjects.attach(Model)`) is read through it, as in
`await Book.aobjects.filter(...).alist()`. `aobjects` does not refuse a swapped-out model; attach it to the model in use.
## MySQL / MariaDB

`django.db.backends.mysql` covers both servers; the driver is asyncmy (`pip install
"django-asyncopation[mysql]"`, which also installs `cryptography`, needed by MySQL 8's default
`caching_sha2_password` login). Asyncopation never imports mysqlclient (`MySQLdb`); note that Django itself still
loads its MySQL backend, and so mysqlclient, while it defines models under the stock `ENGINE`
(ADR-0008). `OPTIONS` are checked against `asyncmy.connect` (keys that only mysqlclient knows
raise `ImproperlyConfigured`, and so does `binary_prefix`, which asyncmy accepts but ignores), and values decode as they do with mysqlclient (`TIME` as
`datetime.time`, zero dates as `None`).

The pool is configured under a separate key, because Django's MySQL backend forwards every `OPTIONS`
key to `MySQLdb.connect` (PostgreSQL keeps using `OPTIONS["pool"]`, which its backend understands):

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        ...,
        "ASYNCOPATION": {
            "pool": {"min_size": 1, "max_size": 10, "timeout": 30.0, "max_waiting": 0,
                     "max_idle": 3600},
        },
    }
}
```

`timeout` is the acquire timeout (`OperationalError`), `max_waiting` limits the acquires in
progress (0: no limit), `max_idle` is how many seconds a connection may stay idle before it is replaced (asyncmy's `pool_recycle`; not a total lifetime like PostgreSQL's `max_lifetime`). Using `ASYNCOPATION` on a PostgreSQL alias raises `ImproperlyConfigured` (PostgreSQL keeps `OPTIONS["pool"]`). Pool sizing defaults are
provisional until the #8 benchmark. A statement cancelled in flight costs a reconnect, and a slow
consumer of an `aiterator()` can lose its connection to the server's `net_write_timeout`. A
`LOCK TABLES` that is not ended with `UNLOCK TABLES` stays on the pooled connection when it is
returned (the driver does not track it), and so does any other session state you set yourself.

## Concurrency and transactions

`async_atomic` (#19) is not available yet: what follows is the ownership
model it will build on (ADR-0001). Outside a transaction every statement will borrow its own
pooled connection, so statements run under `asyncio.gather` execute in parallel. Inside an
`async_atomic` block one connection will be pinned to the task that opened it: using it from
another task (a `gather` or `TaskGroup` child, a task created inside the block), re-entrantly
while a statement or stream is in flight, or after the block ended raises
`asyncopation.ConcurrentQueryError` (a `RuntimeError`, not a `DatabaseError`, so retry loops do
not swallow it). Run concurrent work outside the transaction.

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
`apt install libmysqlclient-dev`) and `uv sync --group mysql-sync`. With mysqlclient installed also
run `ASYNCOPATION_REQUIRE_MYSQLDB=1 uv run pytest tests/unit/django_drift tests/unit/compile`: the
MySQL wrapper drift suite and the "no `MySQLdb` is loaded" checks only mean something there (CI runs
them in the integration and Django-main jobs).

```bash
uv sync --group mysql-sync   # dev + e2e groups + mysqlclient (a plain `uv sync` removes it)
make db-up                  # PostgreSQL, MySQL, MariaDB via docker compose (tmpfs)
make check                  # ruff + ty + import-linter + guards + agent config sync + unit tests
make test-int               # integration tests (ASYNCOPATION_DB=postgresql|mysql|mariadb)
make test-e2e               # boots uvicorn on the harness site
make bench-micro            # in-process micro-benchmarks
make bench                  # k6 load test: WSGI vs ASGI core vs Asyncopation
make help                   # everything else
```

Tooling: uv, pytest (+ pytest-asyncio, pytest-django, pytest-timeout, Hypothesis), ruff (format + lint),
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
