# Architecture

Status: skeleton (Phase 0). Packages exist with their contracts; no behaviour yet. ADR-0001..0007
are Accepted; ADR-0008 is Accepted (scoped `compile_scope` swap).
The plan behind it: [design/plan.md](design/plan.md). Decisions: [adr/](adr/).

## The split

```
QuerySet building (Django, lazy, no I/O)      filter() / order_by() / select_related() ...
            |
            v
SQL generation (Django compiler, sync, CPU)   compiler.as_sql() on a compile-only connection
            |
            v
Evaluation (Asyncopation, async I/O)          driver.execute() -> converters -> Model.from_db
```

`as_sql()` and the expression tree stay synchronous (ecosystem compatibility, no I/O to await).
Everything that touches the network is async and lives in our layers.

## Layers

Imports may only point downwards. Enforced by import-linter (`[tool.importlinter]` in
pyproject.toml) and `tools/guards` (see [.agents/rules/architecture.md](../.agents/rules/architecture.md)).

| Layer | Package | Responsibility | May not |
|---|---|---|---|
| 8 | `asyncopation.testing` | `assert_num_queries` on observers, recording driver | - |
| 7 | `asyncopation.query` | public facade: `aq()`, `AsyncObjects` (`aobjects`), evaluation methods ([ADR-0006](adr/0006-public-api-attachment.md)) | fall through to Django's `sync_to_async` methods |
| 6 | `asyncopation.deletion`, `asyncopation.prefetch` | async `Collector`, async `prefetch_related` as sans-I/O cores | import each other |
| 5 | `asyncopation.fetch` | `afetch()`, async fetch modes, `FETCH_RAISE` default | - |
| 4 | `asyncopation.engine` | `CompiledQuery`, materializer via `results_iter(results=...)`, sans-I/O runner ([ADR-0002](adr/0002-pipeline-contracts.md), [ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)) | import the public facade |
| 3 | `asyncopation.transaction` | `async_atomic`, savepoints, async `on_commit` | - |
| 2 | `asyncopation.connection` | executors (borrowed/pinned), registry keyed by `(alias, loop)`, lifecycle, ASGI lifespan wrapper, hooks ([ADR-0001](adr/0001-connection-ownership.md), [ADR-0004](adr/0004-lifecycle-and-registry.md), [ADR-0005](adr/0005-instrumentation-hooks.md)) | - |
| 1 | `asyncopation.drivers` | `AsyncDriver` protocol, `Capabilities`, psycopg3 / asyncmy implementations ([ADR-0003](adr/0003-driver-contract.md)) | know about models |
| 1 | `asyncopation.compile` | compile-only `DatabaseWrapper` and the synchronous `compile_scope` that swaps it into `connections[alias]` for one compile call ([ADR-0008](adr/0008-compile-only-wrapper-reachability.md)) | do I/O, contain `async def`, `await` inside a scope |
| 0 | `asyncopation._django` | sole importer of non-public Django APIs, split per backend ([ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)) | import anything from `asyncopation` |

## Evaluation pipeline

```
QuerySet -> compile (sync) CompiledQuery -> execute (async) rows -> materialize (sync)
            engine + compile               connection + drivers      compiler.results_iter(results=...)
```

Stage contracts: [ADR-0002](adr/0002-pipeline-contracts.md). Ported orchestration (prefetch,
`Collector`, `get_or_create`) is written as generators that yield requests and is driven by one
async runner in `engine` ([ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)). The research
behind these decisions: [design/software-architecture.md](design/software-architecture.md).

## Invariants

1. No thread hops: no `sync_to_async`, threads or executors in `src/`.
2. No Django sync connection/transaction machinery in `src/`. The one exception is the synchronous
   `compile_scope` swap of `connections[alias]` in `_django.connections` (ADR-0008; no `await` inside).
3. Only `drivers/` imports psycopg / asyncmy.
4. `compile/` never performs I/O; server info is injected by `connection/`. The compile-only wrapper is
   visible through `connections[alias]` only inside a synchronous `compile_scope` (ADR-0008).
5. Every non-public Django import is recorded in [django-api-ledger.toml](django-api-ledger.toml)
   (A201), lives only in `asyncopation._django` (A203) and is still present in the pinned Django
   (drift test; ADR-0007). Every private Django attribute accessed directly is a ledger
   `[[attribute]]` entry protected by the same drift test.
6. Results are identical to the sync ORM on PostgreSQL, MySQL and MariaDB (differential tests).

## Drivers

`drivers/base.py` holds the `AsyncDriver` / `AsyncDbConnection` protocols and the frozen value
types; `drivers/errors.py` ports Django's `DatabaseErrorWrapper` mapping. A driver connection
behaves like Django's sync backend connection ([ADR-0003](adr/0003-driver-contract.md)):

- **PostgreSQL** (`drivers/postgresql`): `psycopg_pool.AsyncConnectionPool` created `open=False` and
  opened in `open()`; every physical connection gets Django's adapters template, `client_encoding`,
  isolation level, `TimeZone` and `assume_role`; rows are tuples. Cursor selection mirrors Django:
  by default `AsyncClientCursor` and, for streams, `ClientCursorMixin` + `AsyncServerCursor`; with
  `OPTIONS["server_side_binding"] = True` `AsyncCursor` / psycopg's `AsyncServerCursor`. Named
  cursors are `scrollable=False` and `withhold` outside transactions. The SQL shape difference
  stays in the compile-only wrapper's `features.uses_server_side_binding`.
- Connections are in autocommit outside `begin()`; a connection returned mid-transaction is rolled
  back. A cancelled statement is cancelled and rolled back (or discarded when its state is unknown);
  a disconnected connection is closed so the pool replaces it.
- `connection_created` is **not** sent for driver connections; hstore/citext registration is
  tracked in #43. The recording driver (`asyncopation.testing.RecordingDriver`) implements the same
  protocol with queued results for unit tests.

## Connection ownership

Per-query borrowing outside transactions, pinning inside `async_atomic`; use of a pinned
connection from another task raises `ConcurrentQueryError`:
[ADR-0001](adr/0001-connection-ownership.md).

## Lifecycle

Configure in `AppConfig.ready()` (no I/O) -> probe server info once per alias -> open pools per
`(alias, loop)` in the ASGI lifespan (`asyncopation.connection.asgi.LifespanApp`) -> close on
shutdown, with a lock-guarded lazy fallback: [ADR-0004](adr/0004-lifecycle-and-registry.md).

## Harness

| Path | Purpose |
|---|---|
| `harness/settings.py` | Django settings for tests, e2e and benchmarks (`ASYNCOPATION_DB`) |
| `harness/catalog/` | test models: every relation kind, a composite-pk model (`BookEdition`) + `FieldZoo` for type conversion |
| `harness/seed.py` | deterministic dataset |
| `harness/site/` | Django site with `/sync`, `/core`, `/aopt` variants per scenario |
| `tests/support/` | differential runner, snapshots, Hypothesis strategies |
| `bench/` | micro-benchmarks, k6 load orchestrator, comparison |
