# Asyncopation - design and roadmap for a truly asynchronous Django ORM

> Status: concept / design draft
> Written: 2026-11-24
> Form: a **third-party package**, not a Django core contribution
>
> Translations: [Korean](plan-KO.md)

| Item | Value |
|---|---|
| Project | **Asyncopation** |
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| Name | syncopation (an off-beat rhythm) + async: a beat that leaves the downbeat (sync). It also nods to the jazz guitarist Django Reinhardt, after whom Django is named |

---

## 1. Background: why Django's async ORM is not "really" asynchronous

### 1.1 Implemented as `sync_to_async` wrappers

Most async methods on `QuerySet` wrap their sync counterparts.

```python
# django/db/models/query.py
async def aget(self, *args, **kwargs):
    return await sync_to_async(self.get)(*args, **kwargs)


async def acreate(self, **kwargs):
    return await sync_to_async(self.create)(**kwargs)
```

- The sync `get()` is delegated to a thread via asgiref's `sync_to_async`; the event loop only awaits the result.
- The event loop never handles socket I/O itself. The design only "sends blocking work to a thread so the loop is not blocked".
- `aiterator()` also calls a `sync_to_async`-wrapped method for every chunk it reads.

### 1.2 `thread_sensitive=True` means effectively serial execution

- `sync_to_async` defaults to `thread_sensitive=True`.
- Django's `ASGIHandler` wraps every request in a `ThreadSensitiveContext`, so **all sync calls within one request run one after another on the same single thread**.

```python
# Expected: three queries in flight at once
# Actual: executed one by one on the same thread
a, b, c = await asyncio.gather(
    User.objects.aget(pk=1),
    Order.objects.acount(),
    Item.objects.afirst(),
)
```

### 1.3 Root cause: the whole DB layer assumes synchronous execution

| Area | Why it is synchronous |
|---|---|
| Connections | `django.db.connections` is thread (context) local. Creation, reuse, `CONN_MAX_AGE` and transaction state are all managed per thread |
| Safety guard | Backend methods carry `@async_unsafe`; calling them directly from the event-loop thread raises `SynchronousOnlyOperation` |
| Transactions | `transaction.atomic` is a sync context manager with no async version |
| Execution path | SQLCompiler -> backend -> cursor, signals and lazy loading are all sync chains |
| Driver | psycopg3 is supported, but only its sync `Connection`. `AsyncConnection` is not integrated |

### 1.4 Practical impact

- Pro: the event loop is not blocked.
- Con: every query pays a thread hop, queries within a request cannot run in parallel, and you get as many connections as threads.
- As a result, ASGI + async ORM is often slower than WSGI + sync on DB-heavy endpoints.

### 1.5 Progress in core

- **#35629 Implement async cursors**: step one opens a low-level API (`new_connection()` + `acursor()`). Making the whole ORM async comes after that.
- **Django 6.1 (2026-08) fetch modes**: introduced `FETCH_ONE` / `FETCH_PEERS` / `FETCH_RAISE`.
- **#37218**: documents that lazy relation access is sync-only. Using `FETCH_PEERS` in async raises `SynchronousOnlyOperation`. The discussion raised the need for `AFETCH_PEERS` / `afetch_{one,many}()`. -> **This is a gap Asyncopation can fill.**

---

## 2. Existing third-party landscape

### 2.1 Projects doing real async I/O

| Project | What it does | Limits |
|---|---|---|
| **django-async-backend** (Arfey) | psycopg3 async cursors, `async_atomic` (nested savepoints, async `on_commit`), most a-methods, `async for` | PostgreSQL only, ASGI only. No `prefetch_related`, `aiterator`, `arefresh_from_db`, related managers |
| **django-turbo-orm** | Builds SQL with Django's `Query`/SQLCompiler on top of django-async-backend and executes it directly on an `AsyncConnectionPool` | Alpha (0.2.0, 2026-01). PostgreSQL only. Performance claims (2-4x) without published methodology |

### 2.2 Async in name only (same limits as core)

- **django-async-orm** (rednaks): patches `async_*` methods onto models; internally delegates to threads. Effectively abandoned since 2023.
- **asgimod**: its author states it is a sync ORM wrapper.

### 2.3 Alternatives outside the Django ORM

- **Tortoise ORM**: a native async ORM with a Django-style API. It does not integrate with Django models, admin or migrations.

### 2.4 What sets Asyncopation apart

1. **MySQL support** (asyncmy). Every existing real-async implementation is PostgreSQL-only.
2. **Async fetch modes**: an async version of Django 6.1 fetch modes (lazy + automatic batch loading).
3. **Async `prefetch_related`**: the area every existing project left empty.

---

## 3. Decided direction

| # | Decision |
|---|---|
| 1 | Drivers: **psycopg3 (PostgreSQL) + asyncmy (MySQL)** |
| 2 | API: leave `objects` untouched and add an **`aobjects`** accessor (a descriptor, not a `Manager` - ADR-0006). Its value is a manager-level object over the default manager: manager methods are proxied, and async manager methods are written on an `AsyncObjects` subclass (ADR-0009) |
| 3 | Scope: support the core traits of the Django ORM, **lazy loading + eager loading** |
| 4 | End goal: make **the SQL generation path async-friendly** as well (defined in §4.4) |

Prerequisites: **Django 6.1+**, **Python 3.12+**, **ASGI only**

---

## 4. Core design

### 4.1 Principle: SQL generation stays Django's, execution goes async

```python
compiler = qs.query.get_compiler(using=qs.db)
sql, params = compiler.as_sql()  # pure SQL generation, no DB I/O
cur = await aconn.execute(sql, params)  # executed on an async driver
rows = await cur.fetchall()
# then port ModelIterable.__iter__ (apply converters -> Model.from_db)
```

- QuerySet building (`filter`, `order_by`, `select_related`, ...) is lazy and does no I/O, so it is reused as-is.
- The async layer intervenes **only at evaluation time**.

### 4.2 Connection ownership model (the project's central design)

- Manage per-request/per-task connections with **`contextvars`** instead of thread-locals.
- Pools: `psycopg_pool.AsyncConnectionPool` and the asyncmy pool.
- **The `asyncio.gather` problem**: child tasks copy the contextvars, so they share the parent's connection, and one psycopg connection cannot run queries concurrently.

| Strategy | Description |
|---|---|
| (a) lock | Serialize one connection with a lock. Safe, but no parallelism |
| (b) per-query borrowing | Outside transactions, borrow from the pool per query and return it |
| (c) transaction pinning | Pin a connection only inside a transaction |

-> **(b) + (c)** is the default, because it gives real parallelism. Accepted in ADR-0001: inside a transaction, use of the pinned connection from another task raises `ConcurrentQueryError` instead of being serialized.

### 4.3 Driver abstraction

The interface assumes two implementations from day one; PostgreSQL is implemented first.

```
AsyncDriver (protocol)                       AsyncDbConnection (protocol)
├── capabilities: Capabilities (frozen)      ├── fetch_all / execute / execute_many
├── open / close                             ├── stream(...) -> async context manager
└── connection() -> borrow                   └── begin / commit / rollback / savepoints
```

The driver behaves like Django's sync backend connection (values, rowcount semantics, session
setup) and translates exceptions to `django.db`; SQL shape stays with Django's `features`
(ADR-0003).

**Per-driver pitfalls**

| Item | PostgreSQL (psycopg3) | MySQL (asyncmy) |
|---|---|---|
| Binding | `AsyncConnection`'s default cursor binds server-side. Use **`AsyncClientCursor`** for Django compatibility (default; `OPTIONS["server_side_binding"] = True` switches to `AsyncCursor`, mirroring Django's sync backend) | `%s` placeholders, compatible |
| `RETURNING` | Supported | MySQL lacks it, so use `lastrowid`. MariaDB supports it, so branch. `bulk_create` pk return also branches |
| Server info | `pg_version` requires a connection | `mysql_server_data` etc. run real queries |
| Type conversion | Almost identical to Django's expectations | Django targets mysqlclient. asyncmy (PyMySQL family) may return different types for datetime/Decimal/JSON -> verified with differential tests |

### 4.4 Redefining "async SQL generation"

**`as_sql()` will not become `async def`.**

- SQL generation is CPU work with no I/O to await; making it async only adds coroutine overhead.
- The Expression/Lookup/Func tree calls `as_sql` recursively. Custom lookups in third-party packages and user code are all sync `as_sql`, so the ecosystem would break.

**Instead, two goals:**

1. **Guarantee zero sync I/O on the SQL generation path**
   - Build a **compile-only `DatabaseWrapper`** that never connects.
   - At startup, query server version/info asynchronously and inject it into the features/ops caches.
   - SQL generation is thereby fully decoupled from sync connection objects.
   - Some Django compilers re-resolve `connections[using]` (aggregate, update) - how they reach the
     compile-only wrapper is ADR-0008 (Accepted: a synchronous, scoped `connections[alias]` swap per compile call).
2. **Rewrite orchestration that mixes I/O and logic as async**
   - `Collector.collect` (runs queries while collecting cascade-delete targets)
   - `get_or_create`, `update_or_create`
   - `prefetch_related_objects`
   - Written as sans-I/O generator cores driven by one async runner, not `async def` copies (ADR-0007).
   - When a second core needs a conversion outside `compile_scope`, generalize `Convert` to carry its own callable so `engine/runner.py` no longer imports `_django.aggregation` (ADR-0007, Decision 1).

### 4.5 Lazy loading

Python attribute access cannot be awaited, so the approach is **explicit async accessors + `FETCH_RAISE` by default**.

```python
books = await Book.aobjects.filter(...).alist()  # aobjects defaults to FETCH_RAISE
books[0].author  # not loaded -> FieldFetchBlocked (fail fast)
await books[0].afetch("author")  # async FETCH_PEERS: fills all peers with one query
books[1].author  # already filled, sync access OK
```

- Descriptors returning awaitables (`await book.author`) are **not adopted**:
  - the return type would depend on whether the relation is loaded;
  - `book.author.name` would break even when `select_related` already loaded it;
  - for reference, SQLAlchemy separated `awaitable_attrs` and `lazy="raise"` for the same reason.
- `afetch` follows the semantics of Django 6.1 fetch modes (`AFETCH_ONE` / `AFETCH_PEERS`).
- If internal APIs such as Django 6.1's peer tracking (weakref list) are reused, **pin the supported Django versions** and run a per-version test matrix.

### 4.6 Eager loading

| Feature | Difficulty | Approach |
|---|---|---|
| `select_related` | Easy | The compiler generates the JOIN and `RelatedPopulator` (pure Python) is reused; only execution is replaced |
| `prefetch_related` | Hard | Port `prefetch_one_level` to async: build querysets with the descriptor's `get_prefetch_querysets` (audited for hidden I/O: the GenericForeignKey variant queries ContentType on a cache miss) -> evaluate asynchronously -> reuse the assignment logic. Must support `Prefetch` objects, `to_attr`, nested lookups and GenericForeignKey |
| related managers | Medium | `author.book_set.filter(...)` is a lazy QuerySet, so an **`aq()` adapter** intervenes only at evaluation |

```python
from asyncopation import aq

books = await aq(author.book_set.filter(published=True)).alist()
```

`aq()` stays the adapter for related managers; the manager-level `aobjects` covers the default manager only (ADR-0009).

---

## 5. Test and benchmark strategy

### 5.1 Differential testing (the core of correctness)

- Run the same QuerySet **through the sync ORM and the async ORM** and compare the results.
- Generate QuerySet combinations (filter/annotate/order_by/select_related/slicing ...) with Hypothesis.
- Run on both PostgreSQL and MySQL - this also catches driver type-conversion differences.

### 5.2 Benchmarks

Compare the same endpoints in three configurations:

1. WSGI + sync ORM
2. ASGI + Django core async ORM (`sync_to_async`)
3. ASGI + Asyncopation

- Tool: k6 or locust
- Metrics: p50/p95/p99 latency, throughput, **DB connection count**, memory
- Scenarios: single lookup, parallel lookups with `gather`, N+1 (`afetch` peers vs `prefetch_related`), writes + transactions

---

## 6. Roadmap

| Phase | Content | Difficulty | Deliverable |
|---|---|---|---|
| 0 | **Test harness**: differential test runner, QuerySet generation strategies, async query capture, CI matrix | Medium | harness, CI |
| 1 | Driver abstraction + compile-only connection + `aobjects` read API (`alist`/`aget`/`afirst`/`acount`/`aexists`/`async for`) - PG -> MySQL. Refines the harness against the real implementation | Medium | first working version |
| 2 | `select_related`, `values` / `values_list` | Medium-low | |
| 3 | **`afetch` + `FETCH_RAISE` by default + async peer loading** (flagship feature) | Medium-high | async fetch modes |
| 4 | Async port of `prefetch_related`, `aq()` adapter | High | |
| 5 | Writes (`acreate`/`asave`/`aupdate`/`adelete`), async `Collector`, `async_atomic`, `Signal.asend` integration | High | |
| 6 | Benchmarks, docs, first public release | Medium | README comparison table |

Tracking: each phase is a GitHub milestone and each roadmap item an issue -
[milestones](https://github.com/devgyurak/asyncopation/milestones).

> Phases 0-1 spend **most of their time on the test harness**. Phases 2-5 then become a loop of "add cases to the harness and make them pass", so the time invested here sets the pace for everything after.

### Phase 0 checklist (test harness)

- [x] Test model set (FK, O2O, M2M, reverse FK, GenericForeignKey, composite pk, many field types) - `harness/catalog`
- [x] Differential test runner: run the same QuerySet sync / async and compare results (instance fields, order, types) - `tests/support/differential.py`
- [x] Hypothesis QuerySet strategies (filter / exclude / order_by / select_related / slicing / annotate / reverse-FK editions / only() and defer()) - `tests/support/strategies.py`
- [ ] Async query capture utility (async `assertNumQueries`, executed SQL log) - needs the driver instrumentation hook from Phase 1 (#9, blocked by #4)
- [x] CI matrix: PostgreSQL / MySQL / MariaDB x supported Django versions (+ Django main canary)
- [x] E2E harness (uvicorn + harness site) and benchmark harness (micro + k6)

### Phase 1 checklist (first spike)

- [x] `alist` working with `as_sql()` + the async driver: `aq(qs).alist()` on PostgreSQL (#6, PR #65; the differential suite on MySQL/MariaDB follows with the MySQL wrapper, #6, PR #68 / #7)
- [x] Port `ModelIterable` logic (converters -> `Model.from_db`), plus the values / values_list / flat / named iterables (`engine.materialize`, #6, PR #65)
- [x] Detect paths that open a sync connection during `as_sql()` (record where `SynchronousOnlyOperation` is raised) - spike results in ADR-0008 (#29, ADR-0008 Accepted)
- [x] Compile-only `DatabaseWrapper` (PostgreSQL) + synchronous `compile_scope` swap (#3, ADR-0008); MySQL/MariaDB composed wrapper (`compile.mysql`, #6, PR #68; asyncmy driver, probe and engine registration -> #7)
- [x] Preload server info asynchronously -> inject into the compile-only connection (PostgreSQL; #5, ADR-0004; MySQL/MariaDB probe -> #7)
- [x] Executors and connection ownership: `BorrowedExecutor` / `PinnedExecutor`, `ConcurrentQueryError`, broken-transaction state, `close()` waiting for pins and streams (#5, ADR-0001; `async_atomic` -> #19, pool sizing -> #8, hooks -> #4)
- [ ] Driver query instrumentation hook (#4, ADR-0005): observers - `QueryEvent`, `asyncopation.observe()` scoped to a task tree, `add_observer()` / `remove_observer()`, fired at the executors' `_run`, zero-subscriber micro-bench (`hooks.*`) - done in PR #74; interceptors (`intercept()`) and the bounded-queue observer follow in the second PR of #4
- [x] hstore/citext type registration in the PostgreSQL driver when `django.contrib.postgres` is installed (#43, ADR-0003 Decision 2); `harness.pgcatalog`
- [x] Pin the asgiref version and `Local(thread_critical=True)` semantics `compile_scope` relies on in the drift suite (ledger `[[dependency]]` row, `asgiref>=3.13`; #49, ADR-0008)
- [x] First differential test passing (`tests/integration/test_harness_selfcheck.py::test_asyncopation_list_matches_sync`) on PostgreSQL, with `aget` / `afirst` / `acount` / `aexists` / `async for`, the `aobjects` descriptor and the sans-I/O runner with the `get_aggregation` / `has_results` cores (#6, PR #67; MySQL/MariaDB with the MySQL wrapper, #6, PR #68 / #7)
- [ ] Manager-level `aobjects` (ADR-0009): manager-only methods proxied and re-wrapped, async manager methods on an `AsyncObjects` subclass, coroutines a manager defines refused (#72)
- [ ] Reproduce the same flow on asyncmy; acceptance (#7): the server-info probe builds `MySQLServerInfo(*row)` from the exact `SELECT` whose column order matches Django's `row[n]` contract (ADR-0008 Decision 5; the source drift test pins the field order)

---

## 7. Code to read first

| File | What to look at | Why |
|---|---|---|
| `django/db/models/sql/compiler.py` | `as_sql`, `execute_sql`, `results_iter`, `get_converters`, Insert/Update/Delete compilers | Where SQL generation and execution split |
| `django/db/models/query.py` | `ModelIterable.__iter__`, `_fetch_all`, `RelatedPopulator`, `prefetch_related_objects`, `prefetch_one_level` | Instance construction, eager loading |
| `django/db/models/fetch_modes.py` | Everything | Structure of Django 6.1 fetch modes |
| `django/db/models/fields/related_descriptors.py` | `fetch_one`, `fetch_many`, `get_prefetch_querysets` | Entry points for lazy/eager loading |
| `django/db/backends/base/base.py`, `postgresql/base.py`, `mysql/base.py` | `DatabaseWrapper`, `async_unsafe`, server info queries | Connection lifecycle, hidden I/O |
| `django/db/transaction.py` | `Atomic` | savepoints, `on_commit` |
| `django/db/models/deletion.py` | `Collector` | Cascade-delete orchestration |

Also read: the django-async-backend source and its `UPSTREAMING.md`, and the core patch for ticket #35629.

---

## 8. References

- Django ticket #35629 - Implement async cursors: https://code.djangoproject.com/ticket/35629
- Django ticket #37218 - Document that lazy relation access is synchronous only: https://code.djangoproject.com/ticket/37218
- Django 6.1 fetch modes docs: https://docs.djangoproject.com/en/6.1/topics/db/fetch-modes/
- Django 6.1 release notes: https://docs.djangoproject.com/en/dev/releases/6.1/
- django-async-backend: https://github.com/Arfey/django-async-backend
- django-turbo-orm: https://pypi.org/project/django-turbo-orm/
- django-async-orm (wrapper family): https://github.com/rednaks/django-async-orm
- Django forum - Asynchronous ORM discussion: https://forum.djangoproject.com/t/asynchronous-orm/5925
