# Architecture

Status: skeleton (Phase 0). Packages exist with their contracts; no behaviour yet. ADR-0001..0007
are Accepted; ADR-0008 is Proposed.
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
| 1 | `asyncopation.compile` | compile-only `DatabaseWrapper` ([ADR-0008](adr/0008-compile-only-wrapper-reachability.md), Proposed) | do I/O, contain `async def` |
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
2. No Django sync connection/transaction machinery in `src/`.
3. Only `drivers/` imports psycopg / asyncmy.
4. `compile/` never performs I/O; server info is injected by `connection/`.
5. Every non-public Django import is recorded in [django-api-ledger.toml](django-api-ledger.toml)
   and lives in `asyncopation._django` (enforced in review until the guard lands; ADR-0007).
6. Results are identical to the sync ORM on PostgreSQL, MySQL and MariaDB (differential tests).

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
| `harness/catalog/` | test models: every relation kind + `FieldZoo` for type conversion |
| `harness/seed.py` | deterministic dataset |
| `harness/site/` | Django site with `/sync`, `/core`, `/aopt` variants per scenario |
| `tests/support/` | differential runner, snapshots, Hypothesis strategies |
| `bench/` | micro-benchmarks, k6 load orchestrator, comparison |
