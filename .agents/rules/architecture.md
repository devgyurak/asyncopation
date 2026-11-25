---
description: Layering, the sync-SQL/async-I/O split and what may live where in src/
paths:
  - "src/**"
---
# Architecture rules

Read `docs/architecture.md` once per session before touching `src/`.

## Core principle
SQL **generation** stays Django's (sync, CPU-only `as_sql()`); only **evaluation** is ours
and it is natively async. Never make `as_sql()` or any expression/lookup method `async`.

## Layers (imports point downwards only - enforced by import-linter)
```
apps           Django AppConfig entry module (ready() -> connection.configure())
testing
query          public facade: aq(), AsyncObjects (aobjects), evaluation methods
deletion | prefetch   sans-I/O cores (Collector, prefetch_one_level)
fetch
engine         CompiledQuery, materializer (results_iter seam), sans-I/O runner
transaction    async_atomic, savepoints, async on_commit
connection     executors (borrowed/pinned), registry (alias, loop), lifecycle, hooks
drivers | compile
_django        sole importer of non-public Django APIs (and the one reader of the app registry, `_django.apps`)
```
- `drivers/` is the **only** place that imports `psycopg`, `psycopg_pool`, `asyncmy`.
  Postgres and MySQL drivers never import each other.
- `compile/` never performs I/O: no `async def`, no imports of drivers/connection/transaction.
  Server version/features are injected into it by `connection/` (ADR-0004); how Django's
  compilers reach it is ADR-0008 (Accepted): a synchronous `compile_scope` swap
  of `connections[alias]` implemented in `_django.connections` (single `swapped` context manager, imported only by
  `compile/scope.py`; plus the read-only `configured_databases()`, imported only by
  `connection/lifecycle.py`), the only A102 exceptions.
- Upper layers talk to drivers only through the `AsyncDriver` protocol. Core code never reads
  vendor strings: SQL shape comes from the compile-only wrapper's `features`, runtime facts from
  `Capabilities` (ADR-0003).
- Non-public Django APIs are imported only in `_django/` (per-backend submodules, imported lazily)
  and every private attribute we access, inside `_django` or not, is a ledger `[[attribute]]`
  entry (ADR-0007).
- Ported orchestration is a sans-I/O generator core driven by the `engine` runner - never an
  `async def` copy of a Django function (ADR-0007).

## Accepted decisions
ADR-0001 ownership, 0002 pipeline contracts, 0003 driver contract, 0004 lifecycle, 0005 hooks,
0006 public API attachment, 0007 sans-I/O ports and `_django`, 0008 compile-only wrapper and
`compile_scope` (`docs/adr/`). Code must follow them;
deviating needs a superseding ADR.

## Forbidden in src/ (tools/guards: A1xx)
- `asgiref.sync` (`sync_to_async`, `async_to_sync`), `threading`, `concurrent.futures`,
  `multiprocessing` - a thread hop is exactly what this project removes.
- `django.db.connection(s)`, `django.db.transaction`, `django.db.router` sync machinery.
- `time.sleep`, `asyncio.run`, `asyncio.get_event_loop`, `run_until_complete`.
- Sync drivers (`psycopg2`, `MySQLdb`, `pymysql`).
- Dynamic imports and code evaluation that bypass the import guards: `importlib` (except
  `from importlib import metadata|resources`), `pkgutil`, `runpy`, `pydoc`, `builtins`,
  `__import__`, `__builtins__`, `eval` / `exec` / `compile`, and the introspection attributes
  `__globals__`, `__closure__`, `__wrapped__`, `__self__`, `__import__`, `__builtins__` (A101 / A103).
- A Django *module* object used as a value (`x = models.base`, `f(models)`, `getattr(models,
  "base")`): it can re-export `connections`; use attribute access only. Django aliases of the
  connection machinery are tracked (A102, `tools/guards/connlint.py`).
- `BaseWrapper` (Django's real PostgreSQL wrapper class) outside `compile/` and `_django/`
  (Django's `DatabaseWrapper` is imported, as `BaseWrapper`, only by `_django/postgresql.py` /
  `_django/mysql.py`; gis backends too); `BaseWrapper` is never renamed on import
  (`BaseWrapper as X`) or rebound (`X = BaseWrapper`), subclassed without `CompileOnlyMixin` first
  or called; the private swap `_django.connections.swapped` outside `compile/scope.py`;
  `ConnectionHandler` / `ConnectionRouter` / `load_backend` anywhere (A102).
- The psycopg names Django modules re-export (`psycopg_any`: `adapt`, `sql`, `ClientCursor`,
  `errors`, `Jsonb`, ranges; `postgresql.base.Database`, `sql`, `adapters`, ...; the `.Database`
  attribute) outside `drivers/` (A104, table in `tools/guards/reexports.py`; the same verdict for
  `from M import n`, `M.n` and `getattr(M, "n")`). Django's own helpers there
  (`get_adapters_template`, `register_tzloader`) are imported only by
  `_django/postgresql_adapters.py` (the psycopg driver's `adapters_template`), which only
  `drivers/postgresql` may import; Django's `mogrify` (it renders through a cursor) is used
  nowhere, a driver cursor's own `.mogrify` only in `drivers/`.
- The guards stop code written by accident, not deliberate evasion (introspection, dunders,
  `sys.modules`, run-time strings): ADR-0008 Decision 6, known limits in `archlint`'s docstring.

## Django coupling
Every Django import outside the public modules listed in `docs/django-api-ledger.toml`
must have a ledger entry (A201) and may only be imported inside `asyncopation/_django/` (A203).
Every private Django attribute accessed directly is a ledger `[[attribute]]` entry protected by
the drift test. See the `port-django-internals` skill.

## Public API
`aobjects` (`AsyncObjects` descriptor), `aq()`, `afetch()`, `async_atomic`, `configure()` / `setup()` /
`close()`, the ASGI lifespan wrapper, `observe()` / `intercept()`, fetch-mode constants and
`asyncopation.testing` (`__all__`: `RecordingDriver`, `RecordingConnection`, `Statement`,
`NoQueuedResult`, `Capabilities`, `ExecResult`). `asyncopation.drivers` is internal and
experimental, not public API.
Changing a public signature needs an ADR (`write-adr` skill) and a CHANGELOG entry.
