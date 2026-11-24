# ADR-0001: Connection ownership - per-query borrowing + transaction pinning

- Status: Proposed
- Date: 2026-11-24
- Deciders: maintainers
- Architect review: pending

## Context
Django's connections are thread-local (plan §1.3). Asyncopation replaces them with
`contextvars` (plan §4.2). Child tasks created by `asyncio.gather` / `TaskGroup` copy the parent
context, so a connection stored in a contextvar would be shared by concurrent tasks, and a single
psycopg/asyncmy connection cannot run two queries concurrently.

## Options
### A - One connection per context, serialized with a lock
+ simple, few connections. - no parallelism inside a request; deadlock risk with nested awaits.

### B - Borrow per query outside transactions
+ real parallelism for `gather`. - one pool checkout per query; read-your-writes only via commit.

### C - Pin a connection inside a transaction
+ transactional semantics. - concurrent queries inside one transaction must be serialized or
rejected.

## Decision (proposed)
B + C. Outside `async_atomic`, every query borrows a pooled connection and returns it when the
result is consumed. Inside `async_atomic`, the transaction pins one connection in a contextvar;
queries from that context use it; concurrent use from gathered child tasks is serialized with a
per-connection lock (open question: reject instead?).

## Open questions
1. Serialize or raise on concurrent use of a pinned connection?
2. Server-side cursors (`aiterator`) hold a connection for the iteration's lifetime - limit?
3. Pool sizing defaults and behaviour on exhaustion (timeout -> `OperationalError`).
4. Behaviour of sync Django code (signals, admin) running in the same request.

## Consequences
Connection count tracks concurrent queries, not threads. Needs pool metrics in
`asyncopation.testing` to assert no leaks.

## Validation
- integration: gathered queries run concurrently (wall time < sum of `pg_sleep`s)
- integration: pinned connection is reused inside `async_atomic`; released on cancel
- bench: `gather` scenario connection peak vs asgi-core
