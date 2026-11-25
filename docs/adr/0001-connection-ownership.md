# ADR-0001: Connection ownership - per-query borrowing + transaction pinning

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-24 (#2, PR #46) - Decision 8: pool options are `OPTIONS["pool"]` (validation rule, `None`/`True`/`False` all mean defaults, size defaults: a missing `max_size` becomes `max(10, min_size)`, `min_size > max_size` raises `ValueError` from `open()`) and `CONN_HEALTH_CHECKS` behaviour (implementation note); approved by: maintainer (@devgyurak), 2026-11-24 in the PR #46 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-25 (#5, PR #52) - Decision 8 (pool sizing defaults are decided by benchmark in #8, not in the ADR-0004 implementation issue; the provisional `min_size=1`, `max_size=10` stay); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #52 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#5, PR #61) - Decision 3 (D1: one public exception in `asyncopation.errors`, three messages; `pin`, `PinnedExecutor` and the owner/re-entrancy/closed checks), Decision 4 (a same-alias pin from a non-owner task raises with its own message), Decision 6c (D2: a broken transaction raises Django's `TransactionManagementError`; the pool drops the closed connection), new Decision 10 (D3: a pin is one in-flight unit; D4: transaction-control passthroughs without policy) and Validation; approved by: maintainer (@devgyurak), to be confirmed in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)

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
   *Amended 2026-11-25 (#5, PR #61) - implementation note (D1):* the exception is
   `asyncopation.errors.ConcurrentQueryError` (a module that imports nothing, so `import asyncopation`
   exposes it without loading Django or a driver) re-exported as `asyncopation.ConcurrentQueryError`.
   One class covers three cases that differ only in message: use from a non-owner task (the message
   above), re-entrant use while a statement or stream is in flight, and any use after the pin block
   exited. The owner is stored as a weak reference to the task; a missing task is a non-owner. The
   pinned executor lives in a contextvar *mapping* (alias -> executor) that is replaced on entry and
   on exit, never mutated; exit removes only its own entry from the *current* mapping (no reset
   token: the block may exit in another Context, e.g. an async generator finalized by another task,
   and a restored copy would revive or drop other pins' entries). A closed executor stays visible to
   child tasks that copied the context (they get the error); `current_executor` hands the owner
   task a borrowed executor again once its own block has ended.
4. **Nested blocks.** `async_atomic` on the same alias in a non-owner task raises (it never opens an
   independent transaction on another connection; *amended 2026-11-25 (#5, PR #61): `pin()` raises
   `ConcurrentQueryError` with its own message*). `async_atomic` on a different alias is
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
      *Amended 2026-11-25 (#5, PR #61) - implementation note (D2):* the executor tracks an open
      transaction from the `begin()` passthrough. Any statement, stream open/read/close or
      transaction-control call (including `BEGIN` and `COMMIT`) cancelled while one is open, and a
      lost connection (`driver.is_disconnect`) while one is open, mark the pinned executor `broken`.
      Every later statement and every transaction-control call except `rollback()` raises
      `django.db.transaction.TransactionManagementError` (the exact type Django's sync `atomic`
      raises for a broken transaction; it is re-exported by `asyncopation._django.transaction`, the
      one name of `django.db.transaction` the guards let through). `rollback()` on a broken executor
      is a no-op that never raises for the broken state (the owner and closed checks still apply),
      so cleanup code cannot replace the error that broke the transaction. The connection itself is
      already closed by the driver (ADR-0003 Decision 7); pin exit does not touch it and the pool
      drops it. A cancelled statement without `begin()` (autocommit) is not broken: the driver
      cancels and rolls back (a cancelled `BEGIN` does mark the executor broken, because the driver
      may already have discarded the connection; a cancelled autocommit stream leaves the driver to
      discard it, and the executor never saw a transaction). A cancelled `COMMIT` leaves its outcome
      unknown and also marks the executor broken.
7. **Streams (`aiterator`).** Streams are closed with `async with` or `aclose()`. Inside a
   transaction they use the pinned connection, so another query while a stream is open raises
   (rule 3) - a documented usability cost, chosen for identical behaviour on PostgreSQL and
   unbuffered MySQL. Closing a stream is exempt from the owner check (async-generator finalizers
   run in another task), and `async_atomic` exit closes any stream still open on its connection.
   Reading a stream after its transaction ended raises.
8. **Pools.** Driver pool defaults, configurable per alias in `DATABASES[alias]["OPTIONS"]`; acquire
   timeout raises `django.db.OperationalError`. Sizing defaults are fixed and benchmarked in #8
   (*Amended 2026-11-25 (#5, PR #52): moved from the ADR-0004 implementation issue to #8, which needs the
   read path of #6*).
   *Amended 2026-11-24 (#2, PR #46) - implementation note:* the PostgreSQL driver reuses
   `DATABASES[alias]["OPTIONS"]["pool"]`: `OPTIONS["pool"]` accepts `None`, `True`, `False` or any
   mapping of `psycopg_pool` `AsyncConnectionPool` keyword arguments (`min_size`, `max_size`,
   `timeout`, `max_lifetime`, ...). `None`, `True` and `False` all mean "use the defaults"
   (provisional `min_size=1`, `max_size=10` until the #8 benchmark *Amended 2026-11-25 (#5, PR #52): the benchmark moved from the ADR-0004 implementation issue to #8*): the async
   side always pools, a deliberate divergence from Django, where `pool=False` means no pool, and the
   driver ignores `CONN_MAX_AGE`. Other types, unknown keys, `close_returns` unless it is exactly
   `False`, and the keys `name`, `configure`, `kwargs`, `conninfo` and `open` (the driver passes
   those itself) are rejected with `ImproperlyConfigured`; `OPTIONS["cursor_factory"]` (not a pool
   key) is still rejected by name. `min_size` and `max_size` must be integers (`max_size` may be
   `None`), `max_waiting` an integer >= 0, `num_workers` an integer >= 1, and `timeout`,
   `max_lifetime`, `max_idle` and `reconnect_timeout` an `int` or `float`; a `bool` is never
   accepted for any of them. `connection_class` is allowed only as a subclass of
   `psycopg.AsyncConnection` - the key must be absent otherwise, and `None` is rejected like any
   other bad value; any other callable value is rejected too (sync hooks do not fit an async pool).
   An explicit `check=None` switches the `CONN_HEALTH_CHECKS` check off. A size the user gives is
   never corrected (a missing `max_size` defaults to `max(10, min_size)`), so `min_size > max_size`
   is not fixed up: `psycopg_pool`'s builtin `ValueError` is raised from `open()` before any
   connection is made (an explicit exception to ADR-0003 Decision 4, which covers database errors;
   this is a configuration error).
   Side effect: Django's own sync backend reads the same key and would also enable its sync pool
   for that alias (`CONN_MAX_AGE` must then stay 0, otherwise Django raises
   `ImproperlyConfigured`). With `CONN_HEALTH_CHECKS` true the driver passes `psycopg_pool`'s
   `check_connection`, which costs one extra round trip per borrow (same meaning as Django's
   pool; referenced from ADR-0004).
9. **Sync code in the same request.** Sync receivers called directly from our coroutines raise
   `SynchronousOnlyOperation`. Code run in a thread (`Signal.asend` sync receivers, user
   `sync_to_async`) uses Django's own sync connection: it is not part of the `async_atomic`
   transaction, cannot see its uncommitted writes and can block on row locks it holds until the
   lock-wait timeout. Documented limitation.

10. **Pin accounting and transaction control** (*added 2026-11-25 (#5, PR #61)*).
    a. (D3) A pin counts as one in-flight unit (`registry.statement_started` /
       `statement_finished`) for its whole lifetime; statements inside it do not count again, so
       `close(timeout)` waits for open pin blocks and open streams up to its timeout and then closes
       anyway. A pin that outlives the timeout is marked closed when `close()` closes the drivers
       (the registry tracks open pinned executors per `(alias, loop)` in a `WeakSet`): its next
       statement or transaction-control call raises `ConcurrentQueryError` ("the drivers were
       closed by close()"; `current_executor` keeps returning the pin to its owner for as long as the
       block is open, so nothing silently runs outside the transaction), and leaving the block
       closes its connection. An executor calls
       `ensure_driver` first and starts its unit right after it returns, with no `await` in between
       (`statement_started` has no closing check); the borrowed executor counts from before the pool
       acquire to after the release, and a stream for its whole lifetime.
    b. (D4) `PinnedExecutor` passes `begin` / `commit` / `rollback` / `savepoint` /
       `release_savepoint` / `rollback_to_savepoint` straight to the connection after the owner,
       re-entrancy and broken checks. It has no policy: nesting, `durable`, `on_commit` and
       `needs_rollback` belong to `async_atomic` (#19). The same task pinning the same alias again
       reuses the executor (`depth` + 1); another task doing so raises (Decision 4); another alias is
       independent.
    c. Every call to a connection from either executor - statements, streams and transaction
       control (`begin` / `commit` / `rollback` / savepoint calls) - goes through one internal
       function (`_run`), the place where the ADR-0005 hooks run (#4); `EVENT_KINDS` maps each call
       to its ADR-0005 Decision 2 kind (`query`, with SAVEPOINT statements, or `transaction` for
       connection-level BEGIN / COMMIT / ROLLBACK). No hook is implemented here.

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
- integration (*added 2026-11-25 (#5, PR #61)*): a statement cancelled inside a pinned transaction
  makes later statements raise `TransactionManagementError` and leaving the pin discards the
  connection; a cancel while waiting in `getconn`, and a cancel racing the acquisition, return the
  connection (6a)
- integration: breaking out of an `aiterator` inside a transaction leaks nothing; reading after the
  transaction ended raises
- bench: `gather` scenario connection peak vs asgi-core
