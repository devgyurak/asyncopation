# ADR-0001: Connection ownership - per-query borrowing + transaction pinning

- Status: Accepted
- Date: 2026-11-24
- Amended: 2026-11-24 (#2, PR #46) - Decision 8: pool options are `OPTIONS["pool"]`
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

## Context
Django's connections are thread-local (plan §1.3). Asyncopation replaces them with
`contextvars` (plan §4.2). Child tasks created by `asyncio.gather` / `TaskGroup` copy the parent
context, so a connection stored in a contextvar would be shared by concurrent tasks, and a single
psycopg/asyncmy connection cannot run two queries concurrently. psycopg 3's `AsyncConnection`
serializes concurrent use internally (an accidental lock); asyncmy does not guard at all and
desyncs its protocol.

Prior art (details in [design/software-architecture.md](../design/software-architecture.md)):
- encode/databases stored the connection in a ContextVar; gathered children shared it and failed
  with "another operation is in progress" until it switched to task-keyed connections (PR #546).
- EF Core, Hibernate Reactive and SQLx forbid concurrent use of one session/connection and report
  it as a programmer error. Only Ecto serializes, and only in an explicit shared mode.
- Tortoise ORM serializes queries on a transaction connection with an `asyncio.Lock`.
- Django core's async work (ticket #35629, PRs #18408 / #19576) and django-async-backend pin one
  connection per task per alias; there is no per-query borrowing.

## Options
### A - One connection per context, serialized with a lock
+ simple, few connections. - no parallelism inside a request; deadlock risk with nested awaits.

### B - Borrow per query outside transactions
+ real parallelism for `gather`. - one pool checkout per query; read-your-writes only via commit.

### C - Pin a connection inside a transaction
+ transactional semantics. - other tasks touching the transaction must be serialized or rejected.

### C1 - Pinned connection: serialize other tasks with a lock (Tortoise)
+ `gather` inside a transaction "just works". - hides a programmer error, statement order becomes
scheduler-dependent, nested awaits can deadlock.

### C2 - Pinned connection: reject use from other tasks (EF Core, Hibernate Reactive, SQLx)
+ deterministic statement order, fails fast with an actionable message, identical behaviour on
psycopg and asyncmy. - work inside `async_atomic` must stay in the task that opened it.

## Decision
**B + C with C2.**

1. **Borrowed executor.** Outside `async_atomic`, every query borrows a pooled connection. For
   buffered results the connection is released after the fetch and before materialization.
2. **Pinned executor.** The outermost `async_atomic` pins one connection in a contextvar and
   records `asyncio.current_task()` as its owner. Both executors implement one protocol.
3. **Owner rule.** Any use of a pinned executor from a task other than its owner raises
   `asyncopation.ConcurrentQueryError`. Re-entrant use within the owner task while a statement or
   stream is in flight also raises. Message: "queries inside async_atomic must run in the task that
   opened it; move concurrent work outside the transaction". The error subclasses `RuntimeError`,
   not `DatabaseError`, so retry loops that catch database errors do not swallow a programmer
   error. A counter in a contextvar is not used: gathered children copy the context.
4. **Nested blocks.** `async_atomic` on the same alias in a non-owner task raises (it never opens an
   independent transaction on another connection). `async_atomic` on a different alias is
   independent and allowed.
5. **Invalidation on exit.** When the outermost block exits, the pinned executor is marked closed;
   any later use (e.g. from a task created inside the block that still holds a copy of the context)
   raises instead of touching a connection that is back in the pool.
6. **Cancellation.**
   a. If a pool acquire completes after the caller was cancelled, the connection is returned.
   b. A connection whose statement was cancelled mid-flight is discarded (asyncmy) or cancelled and
      rolled back (psycopg) - never returned in an unknown state.
   c. Inside `async_atomic`, a cancelled statement marks the transaction broken: later statements
      raise and exit discards the connection.
7. **Streams (`aiterator`).** Streams are closed with `async with` or `aclose()`. Inside a
   transaction they use the pinned connection, so another query while a stream is open raises
   (rule 3) - a documented usability cost, chosen for identical behaviour on PostgreSQL and
   unbuffered MySQL. Closing a stream is exempt from the owner check (async-generator finalizers
   run in another task), and `async_atomic` exit closes any stream still open on its connection.
   Reading a stream after its transaction ended raises.
8. **Pools.** Driver pool defaults, configurable per alias in `DATABASES[alias]["OPTIONS"]`; acquire
   timeout raises `django.db.OperationalError`. Sizing defaults are fixed and benchmarked in the
   ADR-0004 implementation issue.
   *Amended 2026-11-24 (#2, PR #46) - implementation note:* the PostgreSQL driver reuses
   `DATABASES[alias]["OPTIONS"]["pool"]` (`psycopg_pool` keyword arguments: `min_size`, `max_size`,
   `timeout`, `max_lifetime`, ...; `True` means defaults). Provisional defaults are `min_size=1`,
   `max_size=10` until the ADR-0004 implementation benchmark. Side effect: Django's own sync
   backend reads the same key and would also enable its sync pool for that alias (`CONN_MAX_AGE`
   must then stay 0, otherwise Django raises `ImproperlyConfigured`). Keys that do not fit an
   async pool are rejected with `ImproperlyConfigured`: `name`, `reconnect_failed`, `close_returns`, `open`, `kwargs`, `conninfo`, any callable
   value (`check`, `reset`, `configure`, `connection_class`, ...) and `OPTIONS["cursor_factory"]`.
9. **Sync code in the same request.** Sync receivers called directly from our coroutines raise
   `SynchronousOnlyOperation`. Code run in a thread (`Signal.asend` sync receivers, user
   `sync_to_async`) uses Django's own sync connection: it is not part of the `async_atomic`
   transaction, cannot see its uncommitted writes and can block on row locks it holds until the
   lock-wait timeout. Documented limitation.

**Divergence from Django core:** core pins per task; we borrow per query outside transactions to
get real `gather` parallelism. Public names stay aligned with core (`async_atomic(using,
savepoint, durable)`, `on_commit` accepting async callables) so a future migration is mechanical.

## Consequences
Connection count tracks concurrent queries, not threads. `gather` is parallel outside
transactions and an error inside them; features that fan out (prefetch levels, `afetch` batches)
run sequentially when a pinned executor is active (ADR-0007). Frameworks that run downstream code
in a new task (Starlette `BaseHTTPMiddleware`, anyio task groups, `asyncio.shield`) cannot share an
outer `async_atomic`. Leak checks use the registry pool stats from ADR-0004.

## Validation
- integration: gathered queries run concurrently (wall time < sum of `pg_sleep`s)
- integration: pinned connection is reused inside `async_atomic`; released on cancel
- integration: `gather` / `TaskGroup` children querying inside `async_atomic` raise
  `ConcurrentQueryError`; the transaction still rolls back cleanly
- integration: a `create_task` child querying after the block exited raises
- integration: a savepoint opened from a `TaskGroup` child raises
- integration: a statement cancelled mid-query discards the connection (pool stats unchanged)
- integration: breaking out of an `aiterator` inside a transaction leaks nothing; reading after the
  transaction ended raises
- bench: `gather` scenario connection peak vs asgi-core
