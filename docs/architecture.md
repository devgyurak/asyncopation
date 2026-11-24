# Architecture

Status: skeleton (Phase 0). Packages exist with their contracts; no behaviour yet.
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
| 6 | `asyncopation.testing` | async query capture, `assert_num_queries` | - |
| 5 | `asyncopation.deletion`, `asyncopation.prefetch` | async `Collector`, async `prefetch_related` | import each other |
| 4 | `asyncopation.fetch` | `afetch()`, async fetch modes, RAISE default | - |
| 3 | `asyncopation.query` | `aobjects`, `aq()`, iterables (`ModelIterable` port) | import drivers |
| 2 | `asyncopation.transaction` | `async_atomic`, savepoints, async `on_commit` | - |
| 1 | `asyncopation.connection` | contextvars ownership, pools, server-info preload | - |
| 0 | `asyncopation.drivers` | `AsyncDriver` + psycopg3 / asyncmy implementations | know about models |
| 0 | `asyncopation.compile` | compile-only `DatabaseWrapper` | do I/O, contain `async def` |

## Invariants

1. No thread hops: no `sync_to_async`, threads or executors in `src/`.
2. No Django sync connection/transaction machinery in `src/`.
3. Only `drivers/` imports psycopg / asyncmy.
4. `compile/` never performs I/O; server info is injected by `connection/`.
5. Every non-public Django import is recorded in [django-api-ledger.toml](django-api-ledger.toml).
6. Results are identical to the sync ORM on PostgreSQL, MySQL and MariaDB (differential tests).

## Connection ownership

Per-query borrowing outside transactions, pinning inside `async_atomic`:
[ADR-0001](adr/0001-connection-ownership.md) (Proposed).

## Harness

| Path | Purpose |
|---|---|
| `harness/settings.py` | Django settings for tests, e2e and benchmarks (`ASYNCOPATION_DB`) |
| `harness/catalog/` | test models: every relation kind + `FieldZoo` for type conversion |
| `harness/seed.py` | deterministic dataset |
| `harness/site/` | Django site with `/sync`, `/core`, `/aopt` variants per scenario |
| `tests/support/` | differential runner, snapshots, Hypothesis strategies |
| `bench/` | micro-benchmarks, k6 load orchestrator, comparison |
