# Architecture

Status: Phase 1 in progress. The driver layer and the registry/lifecycle (`setup()` / `close()`,
`LifespanApp`, the server-info probe; #5) are implemented for PostgreSQL (psycopg3) together with
the recording driver, and so are the executors (borrowed/pinned connection ownership, #5) and the first read path
(`aq(qs).alist()`: compile, execute, materialize; #6 PR A); `aget` / `afirst` / `acount` /
`aexists` / `aobjects`, the MySQL/asyncmy driver and transactions (`async_atomic`) are still contracts only.
ADR-0001..0007 are Accepted (ADR-0001, 0003, 0004, 0006 and 0007 carry implementation
amendments); ADR-0008 is Accepted (scoped `compile_scope` swap).
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
| - | `asyncopation.errors` | public exceptions of our own (`ConcurrentQueryError`, `SyncEvaluationError`); imports nothing (an import-linter contract keeps it free of `asyncopation` layers, a subprocess import test of Django and the drivers), so `import asyncopation` stays light; re-exported at the package root | import anything |
| 9 | `asyncopation.apps` | Django `AppConfig` (entry module: nothing imports it; `ready()` calls `connection.configure()`; [ADR-0004](adr/0004-lifecycle-and-registry.md)) | - |
| 8 | `asyncopation.testing` | `assert_num_queries` on observers, recording driver (`RecordingDriver`, with `Capabilities` / `ExecResult` re-exported) | - |
| 7 | `asyncopation.query` | public facade: `aq()`, `AsyncObjects` (`aobjects`), evaluation methods ([ADR-0006](adr/0006-public-api-attachment.md)) | fall through to Django's `sync_to_async` methods |
| 6 | `asyncopation.deletion`, `asyncopation.prefetch` | async `Collector`, async `prefetch_related` as sans-I/O cores | import each other |
| 5 | `asyncopation.fetch` | `afetch()`, async fetch modes, `FETCH_RAISE` default | - |
| 4 | `asyncopation.engine` | `CompiledQuery`, materializer via `results_iter(results=...)`, sans-I/O runner ([ADR-0002](adr/0002-pipeline-contracts.md), [ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)) | import the public facade |
| 3 | `asyncopation.transaction` | `async_atomic`, savepoints, async `on_commit` | - |
| 2 | `asyncopation.connection` | executors (borrowed/pinned), registry keyed by `(alias, loop)`, lifecycle, ASGI lifespan wrapper, hooks ([ADR-0001](adr/0001-connection-ownership.md), [ADR-0004](adr/0004-lifecycle-and-registry.md), [ADR-0005](adr/0005-instrumentation-hooks.md)) | - |
| 1 | `asyncopation.drivers` (internal, experimental) | `AsyncDriver` protocol, `Capabilities`, psycopg3 / asyncmy implementations ([ADR-0003](adr/0003-driver-contract.md)) | know about models |
| 1 | `asyncopation.compile` | compile-only `DatabaseWrapper` (`CompileOnlyMixin` + per-backend composition such as `compile.postgresql`, imported lazily; frozen `ServerInfo`) and the synchronous `compile_scope` that swaps it into `connections[alias]` for one compile call ([ADR-0008](adr/0008-compile-only-wrapper-reachability.md)) | do I/O, contain `async def`, `await` inside a scope |
| 0 | `asyncopation._django` | sole importer of non-public Django APIs, split per backend; `_django.transaction` re-exports only Django's `TransactionManagementError` (the one name of `django.db.transaction` guard A101 lets through); `_django.connections` is the only module allowed to touch `django.db.connections` (the `swapped` context manager only, imported by `compile/scope.py` only; guard A102); `_django.apps` is the one place that asks which apps are installed (`is_installed`; read by `connection.engines` at `configure()` for the contrib.postgres flag, ADR-0003 Decision 2); `_django.postgresql_adapters` holds Django's adapter helpers for the psycopg driver (imported by `drivers/postgresql` only; guard A104) ([ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)) | import anything from `asyncopation` |

## Evaluation pipeline

```
QuerySet -> compile (sync) CompiledQuery -> execute (async) rows -> materialize (sync)
            engine + compile               connection + drivers      compiler.results_iter(results=...)
```

Implemented for `alist` (#6 PR A): `engine.compiled.compile_query` (inside `compile_scope`,
`as_sql()` once, `ResultKind.MULTI` / `EMPTY`), `engine.execute.execute_multi` (one `fetch_all` on the
current executor, trimmed to `col_count` for `extra(select=...)`; EMPTY does no I/O),
`engine.materialize.materialize` (the ported post-execute iterable code over
`results_iter(results=[rows])`) and `engine.evaluate.evaluate_list`, which opens the alias first
(the compile-only wrapper needs its server info) and reads `pinned` just before compiling.
Materialization runs outside `compile_scope`: converters, `from_db_value` and `Model.from_db` must
not see the compile-only wrapper (ADR-0008 Decision 2), which the integration tests check by
counting sync connection attempts.

Stage contracts: [ADR-0002](adr/0002-pipeline-contracts.md). Ported orchestration (prefetch,
`Collector`, `get_or_create`) is written as generators that yield requests and is driven by one
async runner in `engine` ([ADR-0007](adr/0007-sans-io-ports-and-django-boundary.md)). The research
behind these decisions: [design/software-architecture.md](design/software-architecture.md).

## Invariants

1. No thread hops: no `sync_to_async`, threads or executors in `src/`.
2. No Django sync connection/transaction machinery in `src/`. There are three exceptions: the
   synchronous `compile_scope` swap of `connections[alias]` in `_django.connections` (ADR-0008; no
   `await` inside), the read-only `configured_databases()` in the same module (ADR-0008 D6, #5a),
   and the re-export of Django's `TransactionManagementError` in `_django/transaction.py`
   (ADR-0007 D5, #5b).
3. Only `drivers/` imports psycopg / asyncmy.
4. `compile/` never performs I/O; server info is injected by `connection/`. The compile-only wrapper is
   visible through `connections[alias]` only inside a synchronous `compile_scope` (ADR-0008).
5. Every non-public Django import is recorded in [django-api-ledger.toml](django-api-ledger.toml)
   (A201), lives only in `asyncopation._django` (A203) and is still present in the pinned Django
   (drift test; ADR-0007). Every private Django attribute accessed directly is a ledger
   `[[attribute]]` entry protected by the same drift test. A third-party package whose runtime
   behaviour we rely on without importing it (asgiref `Local`, ADR-0008) is a `[[dependency]]`
   row: the drift test requires the installed version to equal its `verified_against`.
6. Results are identical to the sync ORM on PostgreSQL, MySQL and MariaDB (differential tests).

## Drivers

`drivers/base.py` holds the `AsyncDriver` / `AsyncDbConnection` protocols and the frozen value
types; `drivers/errors.py` ports Django's `DatabaseErrorWrapper` mapping. A driver connection
behaves like Django's sync backend connection ([ADR-0003](adr/0003-driver-contract.md)):

- **PostgreSQL** (`drivers/postgresql`): `psycopg_pool.AsyncConnectionPool` created `open=False` and
  opened in `open()`; every physical connection gets Django's adapters template (via
  `_django/postgresql_adapters.py`), `client_encoding`,
  isolation level, `TimeZone` and `assume_role`; rows are tuples. Cursor selection mirrors Django:
  by default `AsyncClientCursor` and, for streams, `ClientCursorMixin` + `AsyncServerCursor`; with
  `OPTIONS["server_side_binding"] = True` `AsyncCursor` / psycopg's `AsyncServerCursor`. Named
  cursors are `scrollable=False` and `withhold` outside transactions. The SQL shape difference
  stays in the compile-only wrapper's `features.uses_server_side_binding`.
- **Pool options.** `OPTIONS["pool"]` accepts `None`, `True`, `False` or any mapping of
  `psycopg_pool` `AsyncConnectionPool` keyword arguments (`min_size`, `max_size`, `timeout`,
  `max_lifetime`, ...). `None`, `True` and `False` all mean "use the defaults" (provisional
  `min_size=1`, `max_size=10`; the defaults are decided by benchmark in #8): the async side always
  pools, a deliberate divergence from Django, where `pool=False` means no pool, and the driver
  ignores `CONN_MAX_AGE`. Other types, unknown keys, `close_returns` unless it is exactly `False`,
  and the keys `name`, `configure`, `kwargs`, `conninfo` and `open` (the driver passes those itself)
  are rejected with `ImproperlyConfigured`; `OPTIONS["cursor_factory"]` (not a pool key) is still
  rejected by name. `min_size` and `max_size` must be integers (`max_size` may be `None`),
  `max_waiting` an integer >= 0, `num_workers` an integer >= 1, and `timeout`, `max_lifetime`,
  `max_idle` and `reconnect_timeout` an `int` or `float`; a `bool` is never accepted for any of
  them. `connection_class` is allowed only as a subclass of `psycopg.AsyncConnection` - the key must
  be absent otherwise, and `None` is rejected like any other bad value; any other callable value is
  rejected too (sync hooks do not fit an async pool). An explicit `check=None` switches the
  `CONN_HEALTH_CHECKS` check off. A size the user gives is never corrected (a missing `max_size`
  defaults to `max(10, min_size)`), so `min_size > max_size` is not fixed up: `psycopg_pool`'s
  builtin `ValueError` is raised from `open()` before any connection is made (an explicit exception
  to ADR-0003 Decision 4, which covers database errors; this is a configuration error). Side effect: Django's own sync backend reads the same key and would
  also enable *its* sync pool for that alias (`CONN_MAX_AGE` must then stay 0, otherwise Django
  raises `ImproperlyConfigured`). With `CONN_HEALTH_CHECKS` true the driver passes
  `psycopg_pool`'s `check_connection` (one extra round trip per borrow, as in Django's pool).
- **Open fails fast.** `open()` first connects once directly, so bad credentials or a failing
  `assume_role` raise the same Django exception as the sync backend and leave no pool behind;
  only then is the pool opened (the pool object is built earlier, without I/O, only to validate
  its sizes).
- **Capabilities** (PostgreSQL): the driver owns them. The direct connection of `open()` provides
  `server_version` from its startup parameters (no query) and the driver builds the frozen
  `Capabilities`; `driver.capabilities` is the single owner of runtime facts and is available
  only while the driver is open (`RuntimeError` before `open()` and after `close()`). The
  ADR-0004 probe (once per alias) reads `driver.capabilities` instead of running a second source of
  truth, and the registry builds the alias-level server info from it. For MySQL/MariaDB the probe
  keeps running the full `mysql_server_data` query; the future asyncmy driver's `open()` builds
  `Capabilities` from its own connection and its `server_version` must equal what that probe
  derives. The once-per-alias probe takes the first successfully opened driver's
  `driver.capabilities` (`server_version` is loop-independent) and is retried if that open fails.
- Connections are in autocommit outside `begin()`; a connection returned mid-transaction is rolled
  back, and a failed `COMMIT` restores autocommit. Cancellation: a statement cancelled in
  autocommit is cancelled and rolled back (the connection is discarded when its state is
  unknown). Inside a transaction (autocommit off) or while a stream is open the connection is
  **discarded**: a held cursor survives a rollback, and a silent rollback would continue in a new
  implicit transaction. A stream still open when the borrow ends is `CLOSE`d on release (the
  connection is discarded if `CLOSE` fails). A `CLOSE` that fails when a stream ends inside a
  transaction (e.g. its cursor vanished with a savepoint rollback) raises (as Django does), mapped to
  `django.db` per ADR-0003 D4 (Django leaves it unmapped), unless an earlier error is already
  propagating (the failed cursor is forgotten only when the error is `InvalidCursorName` or the
  cursor is not held; a held cursor stays tracked and `reset()` closes it after the rollback); in
  autocommit it is swallowed and release retries it. A disconnected connection is closed so the pool
  replaces it. A released connection wrapper, and any stream iterator it handed out, raise
  `InterfaceError` when used afterwards.
- `connection_created` is **not** sent for driver connections. With `django.contrib.postgres`
  installed (read at `configure()`), `open()` reads the hstore/citext OIDs once on its first
  connection and every connection registers the handlers from them without I/O (ADR-0003
  Decision 2); the OIDs are not refreshed until the next `close()` / `open()`. The recording driver (`asyncopation.testing.RecordingDriver`) implements the same
  protocol with queued results and a statement log for unit tests (its connections also raise
  `InterfaceError` after the borrow ends); `Capabilities` and `ExecResult` are re-exported from
  `asyncopation.testing`.
- `asyncopation.drivers` is **internal and experimental** (no stability promise); the supported
  public surface for driver-level tests is `asyncopation.testing`.

## Connection ownership

Per-query borrowing outside transactions, pinning inside `async_atomic`; use of a pinned
connection from another task raises `ConcurrentQueryError`:
[ADR-0001](adr/0001-connection-ownership.md).

Implementation (`asyncopation.connection.executor`, internal until `async_atomic` #19 and the engine
#6 use it): both executors implement the `Executor` protocol (`alias`, `pinned`, `fetch_all`,
`execute`, `execute_many`, `stream`) on driver-level types only. `BorrowedExecutor` runs
`ensure_driver` -> `statement_started` (no `await` between) -> `driver.connection()` -> statement ->
release -> `statement_finished` (in `finally`, also on cancellation); a stream holds the connection
and the in-flight count until it ends. `pin(alias)` (an async context manager) borrows one connection,
counts as one in-flight unit for its whole lifetime, records the owner task and registers a
`PinnedExecutor` in a contextvar *mapping* (alias -> executor, replaced never mutated, and a block exit removes only its own entry from the
current mapping; no counter lives in a contextvar because gathered children copy the context). A `PinnedExecutor` rejects, with
`ConcurrentQueryError`, use from another task, re-entrant use while a statement or stream is in
flight and any use after the block exited (a stale child task still sees the closed executor through
its copied context; once `close(timeout)` gave up waiting for a pin it marks the pin closed before
it closes the drivers); it marks itself `broken` when a statement is cancelled inside a transaction
(later statements raise Django's `TransactionManagementError`, `rollback()` is a no-op, the driver
has already discarded the connection - ADR-0003); closing a stream is exempt from the owner check
and pin exit closes the streams still open. It passes `begin` / `commit` / `rollback` / savepoint
calls through with no policy (#19). `current_executor(alias)` returns the contextvar's pinned executor
(even a closed one, so a child's use raises; the owner task gets a borrowed executor again once its
own block ended) or the alias's borrowed executor. Every call to a connection - statements,
streams and transaction control - passes one internal function, `_run` (the ADR-0005 hook point,
#4; `EVENT_KINDS` maps each call to its `query` / `transaction` kind). `ConcurrentQueryError` lives in
`asyncopation.errors`, a module that imports nothing (import-linter contract), and is re-exported at
the package root.

## Lifecycle

Configure in `AppConfig.ready()` (`asyncopation.apps.AsyncopationConfig`, an entry module above
every layer; no I/O) -> open drivers per `(alias, loop)` in the ASGI lifespan
(`asyncopation.connection.asgi.LifespanApp(app)`, which consumes the `lifespan` scope; an app with
its own lifespan calls `setup()` / `close()` itself) -> probe server info once per alias (open -> probe:
PostgreSQL reads `driver.capabilities`, MySQL/MariaDB run the full `mysql_server_data` query) ->
close on shutdown, with a lock-guarded lazy fallback:
[ADR-0004](adr/0004-lifecycle-and-registry.md).

Implementation (`asyncopation.connection`): `configure()` reads Django's *configured* `DATABASES`
through `_django.connections.configured_databases()` (a structure copy: dicts and lists copied, leaves shared; Django fills the defaults in
place) and may be called again until a driver is open or opening (the test runner renames `NAME` after
`ready()`); `engines` maps an ENGINE to its driver factory / probe / injection and imports psycopg
lazily, so `import asyncopation` imports no driver; `registry` holds drivers by `(alias, loop)`,
server info by alias, read-only `PoolStats` and the in-flight statement counter (used by the
executors, `connection.executor`); `lifecycle.ensure_driver(alias)` is the single open path (build -> `open()` ->
probe -> inject, under a per-`(alias, loop)` lock) shared by `setup()` and the lazy fallback;
`close(timeout)` refuses new opens, waits for in-flight statements, open pin blocks and streams, closes the current loop's
drivers and keeps the server info; an open fails closed when `DATABASES` changed after `configure()`
(the test runner's rename: call `asyncopation.configure()` again); whether
`django.contrib.postgres` is installed is also fixed at `configure()` (a later `INSTALLED_APPS`
change needs `asyncopation.configure()` again, ADR-0003 Decision 2); `setup()` opens only aliases with
a supported engine unless names are given; the entries of closed loops are dropped with an ERROR log. MySQL/MariaDB raise `ImproperlyConfigured` at the first open
until #7.

## Harness

| Path | Purpose |
|---|---|
| `harness/settings.py` | Django settings for tests, e2e and benchmarks (`ASYNCOPATION_DB`) |
| `harness/catalog/` | test models: every relation kind, a composite-pk model (`BookEdition`) + `FieldZoo` for type conversion |
| `harness/pgcatalog/` | PostgreSQL-only models (`HStoreField`, `citext`), installed with `django.contrib.postgres` only when `ASYNCOPATION_DB=postgresql` |
| `harness/seed.py` | deterministic dataset |
| `harness/site/` | Django site with `/sync`, `/core`, `/aopt` variants per scenario; on PostgreSQL the ASGI app is wrapped in `LifespanApp`, `GET /diag/pool` shows the registry, and `ASYNCOPATION_APP_NAME` tags the server's database sessions (e2e lifecycle tests count them in `pg_stat_activity`) |
| `tests/support/` | differential runner, snapshots, Hypothesis strategies |
| `bench/` | micro-benchmarks, k6 load orchestrator, comparison |
