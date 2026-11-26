# ADR-0005: Instrumentation hooks - observers and interceptors

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-26 (#4, PR #74) - Decision preamble (subscribers live in `connection.hooks`, not the `(alias, loop)` registry), Decision 1 (event type `QueryEvent`; `query_started` fires after the connection is acquired, right before the driver call, a call that never reaches the driver emits nothing, pinned `queue_time` is 0; subscribers snapshotted at call start and `query_finished` delivered to that snapshot; rowcount per call; descriptive SQL of transaction control; `many` only for `execute_many`; failing observers logged with the event name while a `BaseException` from an observer propagates after the other observers got the event (one from `query_started` stops the call and still delivers `query_finished`; it never breaks a pinned transaction (except a cancellation leaving the block of an open pinned stream, which reaches the driver), which learns what the driver itself did from a per-call `Outcome`, and a control call it stopped before the driver changes no transaction state; on a failed call it becomes a note on the original, and further `BaseException`s of the same event become notes on the first); an observer returning an awaitable is logged and never awaited; `query_time` excludes the time of `query_started` observers), Decision 2 (SAVEPOINT event text is descriptive), Decision 3 (what a stream `error` is; `GeneratorExit` and consumer errors are a normal close; stream `query_time` runs from open to close), Decision 5 (parameters per observer opt-in `observe(fn, params=True)` or `settings.DEBUG`, unconfigured settings counting as off; never to observers that did not opt in), Decision 6 (`observe()` is a single-use context manager - re-entry raises `RuntimeError`, a coroutine function `TypeError` (also from `add_observer`) - whose exit removes its registration by identity and marks it exited instead of a token reset, so a task started inside the block reports to it only until the block exits; global `add_observer` / `remove_observer` in `asyncopation.connection`, a startup-time call); Validation (scope of "a failing observer": raising an `Exception`; an exception an observer raises never breaks a transaction) and new Validation items (snapshot, params opt-in, pool timeout, scope isolation, bench-micro `hooks.*` cases with 0 observers); approved by: maintainer (@devgyurak), 2026-11-26 (the three clarifications of Decisions 1 and 5: start timing, snapshot, params opt-in; all other items - rowcount and the other details from the architect design check, the preamble relocation, the `BaseException` semantics (including a `query_started` observer stopping the call), `query_time` excluding observer time, the awaitable-returning observer, `DEBUG` off for unconfigured settings, the Decision 6 exit semantics (no token reset; a task started inside the block stops reporting when it exits) and the per-call `Outcome` semantics (an observer's exception never breaks a pinned transaction; a BEGIN / COMMIT / ROLLBACK an observer stopped before the driver changes no transaction state; later `BaseException`s of the same event, and those raised on a failed call, become notes) - to be confirmed in the PR #74 review); architect verdict: APPROVE WITH CHANGES (architect review 2026-11-26, final PR #74 text, replacing the earlier verdict whose changes were applied): Decision 2 mark PR #72 -> #74; add the Outcome semantics (observer exceptions never break a pinned transaction, a stopped control call changes no transaction state, later BaseExceptions become notes) to the "to be confirmed" list (changes applied)
- Amended: 2026-11-26 (#4, PR #75) - Decision 1 (with interceptors `query_started` fires before the chain, so a suppressed query emits both events without reaching the driver; the bounded-queue observer is `QueueObserver(maxsize=1000)`: drops the incoming event when full and counts it, bound to the loop it was created in), Decision 4 (dispatch on `context["method"]`, context keys `alias` / `method` / `pinned`; order: global outside scoped, first registered outermost; streams and transaction control including savepoints not interceptable; observers wrap the chain - original SQL, `query_time` / `total_time` include the interceptors (`queue_time` does not), rowcount from the returned result; an interceptor running its own query is intercepted again and holds its connection while borrowing another; the connection is acquired before the chain runs; `TypeError` for a `many` / method mismatch, a `context` that is not a mapping, a non-awaitable interceptor result or a wrong result type; `ConcurrentQueryError` for an `execute` call from another task, `RuntimeError` for one after the query, a sequential retry allowed; a `context["method"]` change only between `execute` and `execute_many`, refused before the driver; an interceptor's exception or cancellation never breaks a pinned transaction, while a driver failure an interceptor swallowed, on any attempt, still does), Decision 6 (`intercept()` and `add_interceptor` / `remove_interceptor` follow `observe()` / `add_observer`; observers and interceptors share the tuples, so the fast path stays two checks) and a new Validation item (interceptor cancellation before / after `execute` inside a transaction, a swallowed driver cancellation still breaking it, `execute` from another task, a refused `context["method"]` change never reaching the driver); approved by: maintainer (@devgyurak), 2026-11-26 (scope: interceptors and the bounded-queue observer in this second PR of #4; the details listed here to be confirmed in the PR #75 review); architect verdict: APPROVE (architect review 2026-11-26, final PR #75 text, replacing the earlier verdict whose changes were applied)

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
**C**, registered on the registry (`asyncopation.connection`, ADR-0004) and fired by the executors
(*Amended 2026-11-26 (#4, PR #74):* the subscribers are held by `asyncopation.connection.hooks` -
a process-wide immutable tuple and a contextvar - not by the `(alias, loop)` registry of ADR-0004,
since an observer is not tied to an alias or a loop):

1. **Observers** are synchronous callables only (awaiting them would add latency and cancellation
   points; not awaiting them would be fire-and-forget, forbidden by async-correctness rule 6).
   Events: `query_started` / `query_finished` with `alias, kind (query | transaction), sql, params,
   many, rowcount, error, queue_time, query_time, total_time`. Exceptions in observers are logged,
   never propagated. Async consumers use a provided bounded-queue observer they drain themselves.
   *Amended 2026-11-26 (#4, PR #75):* `asyncopation.connection.QueueObserver(maxsize=1000)`,
   registered like any observer; when its queue is full the incoming event is dropped and counted
   in `dropped` (it never blocks or schedules); it is bound to the event loop it was created in
   (created inside the running loop), and an event delivered from another loop is dropped and
   counted. It has `get()`, `get_nowait()` and `async for` (which never ends by itself: break out
   or cancel the consumer).
   *Amended 2026-11-26 (#4, PR #74):* both events are one frozen `QueryEvent` (field `name` is
   `query_started` or `query_finished`; times in seconds from `time.perf_counter`; a started event
   has `rowcount`, `error`, `query_time` and `total_time` set to `None`). `query_started` fires
   after the connection is acquired, right before the driver call: a call whose connection is
   never acquired (a pool timeout) emits no event, and `queue_time` (the wait for a pooled
   connection) is 0
   on a pinned connection; `total_time = queue_time + query_time`. The executor snapshots the
   subscribers (global, then scoped) when the call starts, and `query_finished` goes to that same
   snapshot, also when a stream is closed by a finalizer in another task or context.
   *Amended 2026-11-26 (#4, PR #75):* with interceptors, `query_started` fires before the
   interceptor chain (still after the connection is acquired), so a query an interceptor
   suppresses never reaches the driver yet emits both events. `rowcount`:
   `fetch_all` the number of rows returned, `execute` / `execute_many` `ExecResult.rowcount` as is
   (`-1` = unknown), a stream the rows read until it closed (also when left early), transaction
   control and any failed call `None`; `many` is true only for `execute_many` (`params` is the
   batch). Transaction-control events carry a descriptive, backend-neutral `sql` (`BEGIN`,
   `COMMIT`, `ROLLBACK`, `SAVEPOINT <sid>`, `RELEASE SAVEPOINT <sid>`, `ROLLBACK TO SAVEPOINT
   <sid>`; the driver API takes no SQL for them) and no `params`. An observer that raises an
   `Exception` is logged on `asyncopation.connection` with the event name, the other observers
   still run and the query goes on; a `BaseException` is never swallowed: it is raised once the
   other observers got the event (further ones of the same event become notes on it), and when
   it comes from a `query_started` observer the call does
   not reach the driver and `query_finished` is still delivered, with it as `error`. An exception
   an observer raises never marks a pinned transaction broken (the connection never saw it: the
   pinned executor learns what the driver itself raised, and whether the call reached it, from a
   per-call `Outcome`; a BEGIN / COMMIT / ROLLBACK an observer stopped before the driver changes no
   transaction state; the one exception is a cancellation that leaves the block of an open pinned
   stream, which is thrown into the driver's stream and discards the connection), and
   one a `query_finished` observer raises while the call already failed is attached to the
   original exception as a note instead of replacing it. An observer that returns
   an awaitable is logged (a coroutine is closed, never awaited). `query_time` starts after the
   `query_started` observers returned, so their own time is in neither `queue_time` nor
   `query_time`.
2. **Counting semantics** match `CaptureQueriesContext`: SAVEPOINT statements have `kind=query`;
   connection-level BEGIN/COMMIT/ROLLBACK have `kind=transaction`. *Amended 2026-11-26 (#4, PR
   #74):* the `sql` of a SAVEPOINT event is the descriptive text of Decision 1, not the quoted
   statement the backend ran.
3. **Streams:** `query_started` fires when the stream opens; `query_finished` when it closes, with
   the total number of rows read as `rowcount`. *Amended 2026-11-26 (#4, PR #74):* a stream's
   `error` is a failure of the driver (opening, reading a chunk, closing) or a cancellation; an
   exception raised by the consuming code inside the block, and `GeneratorExit` (an async generator
   closed early), are a normal close (`error` is `None`). Its `query_time` runs from open to close,
   so it includes the consumer's time between chunks.
4. **Interceptors** are an async chain with Django's signature `wrapper(execute, sql, params, many,
   context)`; `execute` returns the driver result (`list[tuple]` or `ExecResult`). Returning without
   calling `execute` suppresses the query with a supplied result (test doubles). Streams are not
   interceptable in v1. *Amended 2026-11-26 (#4, PR #75):* they wrap `fetch_all`, `execute` and
   `execute_many`; streams and transaction control, savepoints included (the driver API takes no SQL
   for them), are not interceptable in v1. `context` is `{"alias", "method", "pinned"}` and the
   innermost `execute(sql, params, many, context)` runs the driver method named in
   `context["method"]`: a `many` that does not match it, a method that is not interceptable, an
   interceptor that returns a non-awaitable, a `context` that is not a mapping, and a chain
   result that is not a `list` (`fetch_all`) / `ExecResult` raise `TypeError`. The innermost `execute` must be awaited in the task running
   the query: a call from another task (which is also the only way two calls could overlap) raises
   `ConcurrentQueryError` (ADR-0001 owner rule; the connection never outlives the chain), and a
   call after the query is over raises `RuntimeError`; a sequential retry is allowed (a driver
   failure of an earlier attempt still breaks a pinned transaction). An interceptor may change
   `context["method"]` only to a method with the caller's result type (`execute` <->
   `execute_many`); any other change raises `TypeError` before the driver is called. Global
   interceptors run outside scoped ones; within each, the first
   registered is the outermost (Django's `execute_wrappers` order). The connection is acquired
   before the chain runs (as in Django), so an interceptor that awaits holds it, and test doubles
   still need a driver (`RecordingDriver`). Observers wrap the whole chain: a suppressed or
   rewritten query still emits both events with the caller's SQL, `query_time` and `total_time`
   include the interceptors' time (`queue_time` stays the wait for a pooled connection) and
   `rowcount` follows the result the chain returned. An interceptor that runs a query itself is
   intercepted again (guard against recursion), and on a borrowed connection it borrows a second
   one while holding the first (it can exhaust the pool); inside a pinned block it gets
   `ConcurrentQueryError`. An
   exception or cancellation raised by an interceptor while the driver is not running never marks
   a pinned transaction broken (the per-call `Outcome` of Decision 1 records every failure the driver itself raised,
   on every attempt); a driver failure an interceptor catches and replaces with a result still does.
5. **Parameters** are included in events only when `settings.DEBUG` is true or capture explicitly
   requests them (secrets in logs). *Amended 2026-11-26 (#4, PR #74):* the request is per observer,
   `observe(fn, params=True)` (or `add_observer(fn, params=True)`); an observer that did not opt in
   gets `params=None` even when another observer of the same query opted in, unless
   `settings.DEBUG` is true; settings that are not configured or not loaded yet count as `DEBUG`
   off.
6. **Scoped registration:** `with asyncopation.observe(fn):` / `intercept(fn)` store an immutable
   tuple in a contextvar (set/reset with a token), so capture is per task tree; global registration
   is also available. *Amended 2026-11-26 (#4, PR #74):* `observe()` returns a single-use
   synchronous context manager (entering it twice raises `RuntimeError`; a coroutine function is
   rejected with `TypeError`, by `add_observer` too). Entering sets the contextvar to a new
   tuple; exiting does not reset
   the token (that would bring back a block exited out of order, and fails when the block exits in
   another Context, as an async generator finalized by another task does) but removes its own
   registration by identity from the current tuple and marks it exited, so a Context that still
   holds it stops reporting to it: a task started inside the block (which copied the context)
   reports to it only until the block exits; calls already started keep their snapshot. Global
   registration is `add_observer(fn, *, params=False)` / `remove_observer(fn)` in
   `asyncopation.connection`: a startup-time call, like `configure`, that replaces an immutable
   module-level tuple without a lock (`remove_observer` matches the function by equality, most
   recent registration first). Only `observe` (and `intercept`) are re-exported at the package
   root. *Amended 2026-11-26 (#4, PR #75):* `intercept(fn)` is the same single-use context manager
   and `add_interceptor(fn)` / `remove_interceptor(fn)` the same global registration (in
   `asyncopation.connection`); observers and interceptors share the two tuples, so the
   zero-subscriber path stays two empty-tuple checks.
7. `asyncopation.testing` is built only on these APIs. Materialization (`decode`) timing is not part
   of v1 events.

## Consequences
+ Unblocks #9 without layer violations. + OpenTelemetry integration is an observer, not core code.
- Hooks are on the hot path: with no subscribers the cost must be two empty-tuple checks (global
and scoped), benchmarked.

## Validation
- unit: observer sees every statement of an `alist` with correct SQL; a failing observer does not
  fail the query. *Amended 2026-11-26 (#4, PR #74):* "failing" means raising an `Exception`; a
  `BaseException` is raised after the other observers got the event (Decision 1), and an
  exception an observer raises never marks a pinned transaction broken (the connection never saw
  it; `rollback()` still sends ROLLBACK), except a cancellation that leaves the block of an open
  pinned stream (thrown into the driver's stream, which discards the connection).
- unit: interceptor suppression returns the supplied rows and the driver is never called.
- integration: `assert_num_queries` counts equal Django's `assertNumQueries` for an atomic block
  with savepoints.
- bench-micro: `alist` overhead with zero subscribers within noise of the hook-free baseline.
- *Amended 2026-11-26 (#4, PR #74), added:* unit: a borrowed call whose connection is never
  acquired (pool timeout) emits no event; a stream opened inside `observe()` and closed after the
  block exited, or from another task, delivers `query_finished` to the observers snapshotted at its
  start; parameters reach only observers that opted in (all of them under `settings.DEBUG`);
  scoped observers reach `gather` children, and an `observe()` inside a child reaches neither the
  parent nor a sibling. bench-micro: the `hooks.*` executor cases on a `RecordingDriver` with 0
  observers are within noise of the hook-free baseline.
- *Amended 2026-11-26 (#4, PR #75), added:* unit: inside `pin` + `begin()`, an interceptor that
  raises or is cancelled before calling `execute`, and one cancelled after `execute` returned, leave
  the transaction unbroken and `rollback()` sends ROLLBACK; a driver cancellation inside the chain
  still breaks it, also when an interceptor swallows it; an interceptor that runs `execute` in
  another task gets `ConcurrentQueryError` and the connection is not used after the chain
  returned; a refused `context["method"]` change never reaches the driver.
