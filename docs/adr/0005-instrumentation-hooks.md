# ADR-0005: Instrumentation hooks - observers and interceptors

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

## Context
`asyncopation.testing` (top layer) needs to capture executed SQL (`assert_num_queries`, issue
#9), and users need logging/telemetry. Lower layers must not know about `testing`, and
monkeypatching executors from the top would be fragile. Django's sync path offers
`connection.execute_wrapper()` (`wrapper(execute, sql, params, many, context)`,
`django/db/backends/utils.py`) and `CaptureQueriesContext`, which counts SAVEPOINT statements but
not connection-level BEGIN/COMMIT.

Prior art:
- EF Core separates diagnostics (observe only) from interceptors (`IDbCommandInterceptor`, can
  suppress with `InterceptionResult.SuppressWithResult`). Prisma ADR 271 does the same split.
- Ecto telemetry: one `[:repo, :query]` event with timings and metadata.
- SQLAlchemy events are sync-only under asyncio (registered on `sync_engine`).
- django-async-backend keeps Django's `execute_wrappers` and `queries_log`.

## Options
### A - Monkeypatch-based capture in `testing`
+ no core API. - breaks on refactors, invisible to users, no telemetry story.

### B - Single interceptor chain (Django `execute_wrapper` shape)
+ familiar. - every observer can accidentally alter execution.

### C - Synchronous observers + async interceptors
+ observing is safe, cheap and cannot add await points; interception is explicit.
- two mechanisms to document.

## Decision
**C**, registered on the registry (`asyncopation.connection`, ADR-0004) and fired by the executors:

1. **Observers** are synchronous callables only (awaiting them would add latency and cancellation
   points; not awaiting them would be fire-and-forget, forbidden by async-correctness rule 6).
   Events: `query_started` / `query_finished` with `alias, kind (query | transaction), sql, params,
   many, rowcount, error, queue_time, query_time, total_time`. Exceptions in observers are logged,
   never propagated. Async consumers use a provided bounded-queue observer they drain themselves.
2. **Counting semantics** match `CaptureQueriesContext`: SAVEPOINT statements have `kind=query`;
   connection-level BEGIN/COMMIT/ROLLBACK have `kind=transaction`.
3. **Streams:** `query_started` fires when the stream opens; `query_finished` when it closes, with
   the total number of rows read as `rowcount`.
4. **Interceptors** are an async chain with Django's signature `wrapper(execute, sql, params, many,
   context)`; `execute` returns the driver result (`list[tuple]` or `ExecResult`). Returning without
   calling `execute` suppresses the query with a supplied result (test doubles). Streams are not
   interceptable in v1.
5. **Parameters** are included in events only when `settings.DEBUG` is true or capture explicitly
   requests them (secrets in logs).
6. **Scoped registration:** `with asyncopation.observe(fn):` / `intercept(fn)` store an immutable
   tuple in a contextvar (set/reset with a token), so capture is per task tree; global registration
   is also available.
7. `asyncopation.testing` is built only on these APIs. Materialization (`decode`) timing is not part
   of v1 events.

## Consequences
+ Unblocks #9 without layer violations. + OpenTelemetry integration is an observer, not core code.
- Hooks are on the hot path: with no subscribers the cost must be two empty-tuple checks (global
and scoped), benchmarked.

## Validation
- unit: observer sees every statement of an `alist` with correct SQL; a failing observer does not
  fail the query.
- unit: interceptor suppression returns the supplied rows and the driver is never called.
- integration: `assert_num_queries` counts equal Django's `assertNumQueries` for an atomic block
  with savepoints.
- bench-micro: `alist` overhead with zero subscribers within noise of the hook-free baseline.
