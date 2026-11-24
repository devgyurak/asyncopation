# Software architecture proposal - contracts, seams and lifecycle

- Status: **Research record.** The decisions are ADR-0001..0007 (Accepted) and ADR-0008 (Accepted).
  Where this document and an ADR differ, the ADR wins; the architect review refined several points
  below (layer order, owner rule, observer style, `aobjects` shape, `_django` enforcement).
- Date: 2026-11-24
- Scope: complements [../architecture.md](../architecture.md). That document fixes *which layer
  may import which*; this one proposes *what the layers exchange, where variation lives, how the
  system starts and stops, and how cross-cutting concerns plug in*.

## 1. Why

The layer contract (import-linter) and the forbidden-API guards are in place, but the following
are undefined. Each one will otherwise be decided ad hoc inside the first PR that needs it:

| # | Gap | First blocked issue |
|---|---|---|
| G1 | Data contracts between pipeline stages (compiled query, rows, materialization) | Phase 1 `alist` |
| G2 | Driver interface, backend capability model, where type normalization and error translation live | #4 driver |
| G3 | Lifecycle / composition root (pool creation, server-info preload, shutdown) | Phase 1 preload |
| G4 | Cross-cutting hooks (query capture, logging, telemetry) | #9 query capture |
| G5 | How ported Django orchestration (prefetch, Collector, get_or_create) is structured | Phase 4/5 |
| G6 | How the public API attaches to user models and custom QuerySets | Phase 1 `aobjects` |
| G7 | Concurrency rule for a pinned connection (ADR-0001 open question 1) | Phase 5 `async_atomic` |

## 2. Prior art surveyed

| Project | Closest to us in | Key lesson |
|---|---|---|
| **ormar** (native backend, 0.30) | Same split: SQLAlchemy compiles, SQL runs directly on asyncpg/asyncmy | `StatementPlan` cache, `DriverAdapter` ABC, translate driver errors into the *same* exceptions as the sync path, drift tests over the private compiler attributes it uses |
| **Hibernate Reactive** | Reuses a sync ORM's SQL generation, swaps the I/O layer | `ResultSetAdaptor` feeds async rows into unchanged core result processing. Counter-lesson: it forked ~309 of 446 core classes into `Reactive*` copies and pins every release to one ORM minor |
| **SQLAlchemy 2.x asyncio** | Async over a sync core | Dialect capability flags overwritten per instance at `first_connect` (MariaDB vs MySQL by server version); MRO-based exception translation; event hooks. Uses greenlet - rejected for us (section 4) |
| **psycopg 3** | Sharing logic between sync and async | Sans-I/O generators (`yield Wait.R`) driven by `wait()` / `wait_async()`; pools created with `open=False` and opened explicitly (opening in the constructor is deprecated) |
| **django-async-backend** (DAB) and Django PRs #18408 / #19576 (ticket #35629) | Where Django core is heading | Vocabulary: `async_connections`, `async_atomic(using, savepoint, durable)`, `on_commit` with async callables, `features.supports_async`. Owns one connection per task; monkeypatches `Model` and uses a fixed manager (custom QuerySets lost) |
| **django-turbo-orm** | Same idea, done without contracts | Skips Django converters (`from_db_value` never runs); every query in `async_atomic` takes a *different* pool connection, so atomic blocks are not atomic |
| **Tortoise ORM** | Native async Django-like ORM | Frozen `Capabilities`, `fetch_related` + `NoValuesFetched`, per-driver exception translation. Pools in contextvars broke under ASGI lifespan (needed a global fallback); capabilities static per class (no MariaDB) |
| **Piccolo** | Engine choosing transaction / pool / fresh connection | Same selection order as our borrow-or-pin model; counter-lesson: I/O in `Engine.__init__` |
| **encode/databases** (archived) | contextvars connection ownership | ContextVar-inherited connections were shared by gathered child tasks ("another operation is in progress"); fixed by task-keyed connections. Leaked raw driver exceptions; coupled to private SQLAlchemy APIs |
| **Ecto** | Ownership and explicit loading | `prepare` (pure, cacheable) vs `execute` (I/O); `NotLoaded` sentinel; `preload(in_parallel)` only outside a transaction; `Ecto.Multi` = transaction as inspectable data; telemetry measurement set |
| **EF Core** | Concurrency detection, hooks | "A second operation started on this context before a previous operation completed" - detected with an in-flight flag + async-flow owner; observers (diagnostics) vs interceptors (can suppress, `SuppressWithResult`) |
| **SQLx / SeaORM** | Executor types | One executor protocol implemented by pool, connection and transaction; `&mut` makes concurrent use of one connection impossible; `MockDatabase` transaction log for SQL assertions |
| **Prisma (ADRs 002/031/159/187/271)** | Plans, capabilities, lifecycle | Immutable plans; capabilities = static + runtime probe, core never asks "which vendor"; driver states create -> connect -> acquire; results invalidated when their transaction ends; observe vs intercept middleware |

## 3. Proposal

### 3.1 Pipeline contracts (G1) - reuse Django's result path through a row seam

```
QuerySet (Django, lazy)
  -> compile (sync, layer 0)      CompiledQuery      frozen: sql, params, result_kind,
                                                     col_count, returning, compiler
  -> execute (async, via Executor) RowBatch | AsyncRowStream   driver-normalized tuples
  -> materialize (sync)           compiler.results_iter(results=[rows])  -> converters
                                  -> ModelIterable post-execute logic -> Model.from_db
```

- **The seam is `SQLCompiler.results_iter(results=...)`.** When `results` is given, Django does
  not execute; it applies `get_converters`/`apply_converters` and composite-field handling. Our
  port of `ModelIterable.__iter__` is therefore only the part *after* `execute_sql`, and
  `from_db_value` keeps working (the bug turbo-orm has). This is Hibernate Reactive's
  `ResultSetAdaptor` idea without its fork.
- Stage values are frozen dataclasses (Prisma ADR 002, ormar `StatementPlan`). A `CompiledQuery`
  is produced and consumed in the same task; it is not cached across requests in v1 (Django's
  `as_sql` cost is measured first, `perf-bench`).
- Streams (`aiterator`) are bound to their connection and raise when read after the owning
  transaction ended (Prisma ADR 187) instead of returning stale data.

### 3.2 Driver contract and capabilities (G2)

```python
class AsyncDriver(Protocol):  # one per backend; only module importing the driver lib
    capabilities: Capabilities

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def connection(self) -> AbstractAsyncContextManager[AsyncDbConnection]: ...  # borrow


class AsyncDbConnection(Protocol):
    async def fetch_all(self, sql: str, params: Params) -> list[tuple]: ...
    async def execute(
        self, sql: str, params: Params
    ) -> ExecResult: ...  # rowcount, lastrowid, returned
    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult: ...
    def stream(self, sql: str, params: Params, chunk: int) -> AsyncIterator[list[tuple]]: ...
    async def begin(self) -> None: ...  # + commit, rollback, savepoint, release, rollback_to


@dataclass(frozen=True)
class Capabilities:  # static per driver, refined by the server probe
    vendor: str
    server_version: tuple[int, ...]
    insert_returning: bool
    bulk_insert_returning: bool
    server_side_cursors: bool
```

- **Core code reads capabilities, never vendor names** (Prisma ADR 031). MariaDB >= 10.5
  `RETURNING` is a probed capability, not a subclass (SQLAlchemy `initialize`).
- **Type normalization lives in the driver.** Contract: a driver returns the same Python types
  Django's own backend driver would (psycopg: connect with Django's
  `get_adapters_template(use_tz, tz)`; asyncmy: a decoder shim matching mysqlclient). Django's
  backend converters then run unchanged. Differential tests are the acceptance test.
- **Error translation lives in the driver**: one MRO-based map to `django.db` exceptions
  (SQLAlchemy `DBAPIError.instance`, Tortoise `translate_exceptions`), plus `is_disconnect`
  to discard broken connections. No raw driver exception crosses layer 0.
- A **recording driver** (in-memory, queued results, statement log - SeaORM `MockDatabase`)
  implements the same protocol for unit tests of upper layers.

### 3.3 Ownership and concurrency (G7, refines ADR-0001)

- Two executor kinds behind one protocol (SQLx): **Borrowed** (outside a transaction: acquire per
  query, release when the result is consumed) and **Pinned** (inside `async_atomic`).
- **Pinned connections reject use from other tasks instead of serializing** (EF Core, Hibernate
  Reactive, SQLx; Ecto serializes only in an explicit shared mode). Detection: an in-flight flag
  on the connection plus an owner token = `asyncio.current_task()`. A counter in a contextvar is
  not enough, because `gather`/`TaskGroup` children copy the context (encode/databases bug class).
  Error text names the fix: "use sequential awaits inside async_atomic, or run the work outside the
  transaction".
- Consequence for features: prefetch levels and `afetch` batches run sequentially inside a
  transaction and may run in parallel outside one (Ecto `preload(in_parallel)`).
- Pool `acquire` is shielded from cancellation, and release always runs (DAB `UPSTREAMING.md`).
- **Divergence from Django core to record in ADR-0001:** core/DAB pin one connection per task;
  we borrow per query outside transactions. Keep the names (`async_atomic`, `on_commit`) aligned.

### 3.4 Lifecycle and composition root (G3)

```
create (import / AppConfig.ready: no I/O)  ->  open (ASGI lifespan: pools open, server probe,
inject server info into compile-only wrapper)  ->  serve  ->  close (lifespan shutdown)
```

- A process-global **registry** keyed by `(alias, event loop)` owns drivers and pools - never a
  contextvar (Tortoise lifespan bug) and never created at import (Piccolo).
- `await asyncopation.setup()` / `close()` plus an ASGI lifespan helper. If a query arrives before
  setup, a lock-guarded lazy `open` runs once-unless-exception (SQLAlchemy `first_connect`), so
  tests and scripts still work; a warning nudges production users to the lifespan hook.
- The probe fills Django's cached server data (`pg_version`, `mysql_server_data`, features) on the
  compile-only wrapper *before* the first `as_sql()`, so no sync connection can ever be opened.

### 3.5 Cross-cutting hooks (G4)

- **Observers** (cannot change anything): `query_started`/`query_finished` events with Ecto's
  measurements (queue, query, total time) and `alias, kind, sql, params, rowcount, error`.
  Synchronous (ADR-0005); this is what `asyncopation.testing` subscribes to - no monkeypatching, which
  unblocks #9.
- **Interceptors** (may rewrite or suppress): an `execute_wrappers` chain in Django's shape,
  `wrapper(execute, sql, params, many, context)`, so existing Django instrumentation idioms carry
  over. `SuppressWithResult` (EF Core) makes test doubles possible.
- Both are registered on the registry (layer 1) and fired by the executor; layer 6 only consumes.

### 3.6 Ported orchestration as sans-I/O cores (G5)

```python
def prefetch_one_level_plan(...) -> Generator[CompiledQuery, list[tuple], PrefetchResult]: ...
async def run(plan, executor):          # the only async part, ~10 lines, shared by all cores
    ...
```

- Prefetch, `Collector`, `get_or_create` become generators that **yield queries and receive
  rows** (psycopg `generators.py`). Logic is written once, has no `await`, and is unit-testable
  with a recording driver. A sync runner over Django's own cursor allows differential testing of
  the *core itself* against upstream.
- The deletion `Collector` produces an inspectable plan before executing (Ecto.Multi).
- Fork surface stays small: copy only the I/O-interleaved functions, each with a ledger entry
  (Hibernate Reactive's 309 forked classes are the failure mode).

### 3.7 Public API attachment (G6)

- **No monkeypatching of `Model`, no fixed manager** (DAB loses custom QuerySets).
- `aq(qs)` wraps any QuerySet or Manager by composition. Chaining methods (including user-defined ones that
  return a QuerySet) are proxied and re-wrapped; only evaluation methods (`alist`, `aget`, ...)
  are ours. `aobjects` is a plain descriptor (`AsyncObjects`, not a `Manager`) returning
  `aq(owner._default_manager.get_queryset())`, so a model's custom QuerySet methods are available
  on `aobjects` too (ADR-0006).
- Relation loading: Django 6.1's RAISE fetch mode is the `NotLoaded` sentinel (Ecto, Tortoise
  `NoValuesFetched`); `afetch` is explicit and non-recursive (Hibernate Reactive), peers batched
  and aligned to parents (SeaORM loader).

### 3.8 Django internals boundary

- All non-public Django imports go through one package, `asyncopation._django`, split per backend.
  import-linter cannot target subpackages of an external package, so enforcement is a
  `tools/guards` check (A203, ADR-0007). The ledger stays the inventory.
- A drift test (driven by the ledger's `[[internal]]`, `signatures` and `[[attribute]]` entries)
  asserts every private attribute we touch still exists on the pinned Django
  version. `ported` entries are checked against the module source instead of imported (ormar `test_sqlalchemy_internals.py`), so Django upgrades fail loudly in one place.

## 4. Rejected options

| Option | Why not |
|---|---|
| greenlet bridge (SQLAlchemy) | Would run Django's sync code with hidden I/O inside sync frames (violates non-negotiable 1 in spirit); Django's `async_unsafe` and thread-local `ConnectionHandler` still have to be bypassed; C-extension packaging/free-threading risk; `MissingGreenlet`-style runtime failures |
| Whole-file codegen of Django modules (DAB) | Large generated surface pinned to one Django release; reviewing diffs is harder than targeted ports + ledger |
| Wholesale fork of executors/persisters (Hibernate Reactive) | Maintenance cost proven by its ORM 5 -> 6 migration |
| Own row materialization (turbo-orm) | Skips `from_db_value`; breaks differential correctness |
| Serializing a pinned connection with a lock (Tortoise) | Hides programmer errors and deadlock-prone with nested awaits; reject + clear error instead |

## 5. Proposed layer adjustments

```
testing
query          public facade: aq(), aobjects, evaluation methods
deletion | prefetch
fetch
engine         CompiledQuery contract, materializer, sans-I/O runner                (new)
transaction
connection     registry, lifecycle, executors (borrowed/pinned), hooks
drivers | compile
_django        sole importer of non-public Django APIs                              (new)
```

`query` previously mixed the public facade with the execution engine; splitting `engine` out keeps
the facade thin and lets `fetch`/`prefetch`/`deletion` reuse the engine without importing the
facade. The facade sits at the top because evaluation runs prefetch and `adelete` runs deletion.

## 6. ADRs

| ADR | Decision | Blocks |
|---|---|---|
| 0008 (Accepted) | How Django compilers that re-resolve `connections[using]` reach the compile-only wrapper; MySQL without mysqlclient | Phase 1 `acount`, writes |
| 0001 (update) | Reject concurrent use of a pinned connection; owner token; record divergence from Django core | Phase 5 |
| 0002 | Pipeline contracts and the `results_iter(results=...)` seam | Phase 1 `alist` |
| 0003 | Driver protocol, capabilities, type normalization + error translation in drivers | #4 |
| 0004 | Lifecycle, registry keyed by `(alias, loop)`, server probe | Phase 1 preload |
| 0005 | Hooks: observers vs interceptors | #9 |
| 0006 | Public API attachment (`aq` composition, `aobjects` descriptor) | Phase 1 `aobjects` |
| 0007 | Sans-I/O cores for ported orchestration; `_django` boundary + drift tests | Phase 4/5 |

## 7. Sources

- ormar native backend: https://github.com/ormar-orm/ormar/tree/master/ormar/databases/native
- Hibernate Reactive: https://github.com/hibernate/hibernate-reactive ,
  https://docs.hibernate.org/reactive/3.0/reference/html_single/
- SQLAlchemy asyncio: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html ,
  `lib/sqlalchemy/util/concurrency.py`, `lib/sqlalchemy/engine/create.py`
- psycopg 3: `psycopg/generators.py`, `psycopg/waiting.py`, https://www.psycopg.org/psycopg3/docs/advanced/pool.html
- Django ticket #35629, PRs https://github.com/django/django/pull/18408 and
  https://github.com/django/django/pull/19576 , DAB https://github.com/django-async/django-async-backend
- Tortoise ORM: https://github.com/tortoise/tortoise-orm ; Piccolo: https://github.com/piccolo-orm/piccolo
- encode/databases PR #546: https://github.com/encode/databases/pull/546
- Ecto: https://ecto.hexdocs.pm/Ecto.Adapter.Queryable.html , https://ecto-sql.hexdocs.pm/Ecto.Adapters.SQL.Sandbox.html
- EF Core: https://learn.microsoft.com/en-us/ef/core/logging-events-diagnostics/interceptors ,
  `src/EFCore/Infrastructure/Internal/ConcurrencyDetector.cs`
- SQLx / SeaORM: https://docs.rs/sqlx/latest/sqlx/trait.Executor.html , https://www.sea-ql.org/SeaORM/docs/write-test/mock/
- Prisma ADRs: https://github.com/prisma/prisma/tree/main/docs/architecture%20docs/adrs
