---
description: Event-loop safety, connection ownership, cancellation and concurrency rules
paths:
  - "src/**"
---
# Async correctness rules

1. **Never block the loop.** No sync I/O in coroutines - including Django calls that may
   lazily open a sync connection (`connection.features`, `pg_version`, `mysql_server_data`).
   If Django code you call might touch a connection, route it through the compile-only wrapper.
2. **Ownership through `contextvars`, never thread-locals.** A task inherits a *copy* of the
   context; children spawned by `asyncio.gather`/`TaskGroup` see the parent's values.
3. **One connection = one in-flight query.** Outside a transaction borrow a pooled connection
   per query and return it in `finally`/`async with`. Inside `async_atomic` the connection is
   pinned; concurrent queries on a pinned connection must be serialized or rejected - follow
   ADR-0001 (`docs/adr/0001-connection-ownership.md`) and do not invent a new strategy in code.
4. **Cancellation safety.** Every acquire has a matching release on `CancelledError`.
   Rollback/release paths must not be skipped by cancellation; never swallow `CancelledError`.
5. **No hidden global state.** Pools are keyed by DB alias and created lazily inside the running
   loop; never create pools at import time or bind them to a loop that may be closed.
6. **Structured concurrency.** Prefer `asyncio.TaskGroup`; never fire-and-forget tasks.
7. **Resource leaks are bugs.** Cursors, server-side cursors and pooled connections are always
   closed via `async with`. Tests should assert pool stats where practical.
8. **Errors map to Django's.** Translate driver exceptions into `django.db` exception classes
   (`IntegrityError`, `OperationalError`, ...) so user code behaves the same as with sync ORM.
