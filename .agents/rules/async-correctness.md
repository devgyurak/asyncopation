---
description: Event-loop safety, connection ownership, cancellation and concurrency rules
paths:
  - "src/**"
---
# Async correctness rules

1. **Never block the loop.** No sync I/O in coroutines - including Django calls that may
   lazily open a sync connection (`connection.features`, `pg_version`, `mysql_server_data`).
   If Django code you call might touch a connection, route it through the compile-only wrapper
   (ADR-0008: `compile_scope` swaps it into `connections[alias]` synchronously; never `await` inside).
2. **Ownership through `contextvars`, never thread-locals.** A task inherits a *copy* of the
   context; children spawned by `asyncio.gather`/`TaskGroup` see the parent's values.
3. **One connection = one in-flight query.** Outside a transaction borrow a pooled connection
   per query and return it in `finally`/`async with` (buffered results: release before
   materialization). Inside `async_atomic` the connection is pinned to the task that opened it;
   use from any other task, or re-entrant use while a statement/stream is in flight, raises
   `ConcurrentQueryError` - never serialize with a lock. The pinned executor is invalidated on
   exit (use afterwards raises), a pin counts as one in-flight unit, an executor starts its unit
   right after `ensure_driver` returns (no `await` between) and finishes it exactly once, and
   closing a stream is exempt from the owner check. A cancelled statement inside a transaction
   marks it broken (`TransactionManagementError`). No counter lives in a contextvar; the pinned
   executor sits in a contextvar mapping that is replaced, never mutated. Follow ADR-0001
   (`docs/adr/0001-connection-ownership.md`); do not invent a new strategy.
4. **Cancellation safety.** Every acquire has a matching release on `CancelledError`, including
   an acquire that completes after the caller was cancelled. A connection whose statement was
   cancelled mid-flight is discarded or reset, never returned as-is; inside a transaction it marks
   the transaction broken. Never swallow `CancelledError`. A core's cleanup (`core.close()` in
   the runner) never replaces the original exception: it is attached as a note with `add_note`
   and the original is re-raised. A `ScopeContextError` and a `BaseException` from that cleanup
   (a nested cancellation, `KeyboardInterrupt`) propagate. Elsewhere, a cleanup error is
   suppressed or logged, never raised over the original.
5. **No hidden global state.** Drivers and pools live in the registry keyed by `(alias, loop)`
   and are created inside the running loop; server info is probed once per alias. Never create
   pools at import time, never store them in a contextvar (ADR-0004).
6. **Structured concurrency.** Prefer `asyncio.TaskGroup`; never fire-and-forget tasks.
   Instrumentation observers are synchronous; nothing in the hot path schedules tasks (ADR-0005).
7. **Resource leaks are bugs.** Cursors, server-side cursors and pooled connections are always
   closed via `async with`. Tests should assert pool stats where practical.
8. **Errors map to Django's.** Drivers translate exceptions into `django.db` exception classes
   (`IntegrityError`, `OperationalError`, ...) with `DatabaseErrorWrapper` semantics so user code
   behaves the same as with the sync ORM (ADR-0003).
