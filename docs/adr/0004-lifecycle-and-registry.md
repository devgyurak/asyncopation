# ADR-0004: Lifecycle, driver registry and server-info preload

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-24 (#2, PR #46) - Decision 2 (the driver, not the probe, builds `Capabilities`; PostgreSQL probe reads `driver.capabilities`; the once-per-alias probe takes the first successfully opened driver's capabilities; MySQL/MariaDB asyncmy driver builds them from its own connection and the probe result must equal it), Decision 3 (`open()` connects directly first: fail-fast, order open -> probe), Decision 4 (same order on the lazy path), new Validation item for MySQL/MariaDB; approved by: maintainer (@devgyurak), 2026-11-24 in the PR #46 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-25 (#5, PR #52) - Decision 1 (`configure()` is re-callable until a driver is open or opening and reads the configured settings through `_django`; `asyncopation.apps` is a top-level entry module; a per-alias preparation failure or unsupported ENGINE is recorded and raised at that alias's first open, never in `ready()`; an open fails closed when `DATABASES` changed since `configure()`), Decision 2 (the probe is a pure synchronous function published with `setdefault`; server info survives `close()`), Decision 3 (`setup()` opens only aliases with a supported engine by default; explicit aliases must be usable; all or nothing for the drivers it opened), Decision 5 (`close(timeout)` semantics, bounded wait for an open in progress, overlapping closes), Decision 7 (`LifespanApp(app)` consumes the `lifespan` scope and does not forward it; no `aliases` argument), Consequences (loops that end without `close()`), new Implementation note (pool sizing -> #8, see ADR-0001 D8) and Validation items; approved by: maintainer (@devgyurak), 2026-11-25 in the PR #52 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, re-review after the maintainer's LifespanApp scope decision, changes applied)
- Amended: 2026-11-25 (#5, PR #61) - Decision 5 (`close()` also waits for open pin blocks and streams); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#7, PR #70) - Decision 2 (the probe is the same pure synchronous function for every engine: MySQL/MariaDB read `capabilities` and `driver.server_data`; no engine's probe runs a query), Decision 5 (asyncmy close sequence), Decision 6 (`PoolStats.waiting` differs per driver), Validation; approved by: maintainer (@devgyurak), 2026-11-25 (issue #7a design session); architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)

## Context
The compile-only `DatabaseWrapper` must know the server version/features before the first
`as_sql()`, otherwise Django opens a sync connection (`pg_version`, `mysql_server_data`,
`connection.features`). Pools must be created inside the running loop (async-correctness rule 5).
Django's `ASGIHandler` does not handle the `lifespan` scope (`django/core/handlers/asgi.py`:
"Django can only handle ASGI/HTTP connections"). Nothing defines who creates drivers, when the
probe runs, or how shutdown happens.

Prior art:
- psycopg_pool: construct with `open=False`, `await pool.open(wait=True)` in the lifespan,
  `await pool.close()`; opening in the constructor is deprecated (wrong-loop asyncio objects).
- SQLAlchemy: `first_connect` runs `dialect.initialize()` once-unless-exception; engines must not be
  shared across loops; explicit `await engine.dispose()`.
- Tortoise: connections in a contextvar were invisible to requests because lifespan runs in a
  different task (`_enable_global_fallback`, issues #1694, #1676).
- Piccolo: I/O in `Engine.__init__` caused loop problems.
- Prisma ADR 159: create (no I/O) -> connect -> acquire; use before connect fails fast.

## Options
### A - Lazy everything (open pool and probe on first query)
+ zero configuration. - first request pays the probe; failures surface mid-request.

### B - Explicit lifecycle only (`setup()` in the lifespan, error if missing)
+ predictable. - scripts and tests must call setup; easy to forget.

### C - Explicit lifecycle with a lock-guarded lazy fallback
+ production uses the lifespan; scripts/tests still work. - two open paths to keep identical.

## Decision
**C.**

1. **Configure** (`AppConfig.ready()`; `asyncopation` must be in `INSTALLED_APPS`): build per-alias
   config/factory objects from `DATABASES`. No database I/O, no asyncio objects (and never inside the
   running loop: it reads the tz database from disk; *Amended 2026-11-25 (#5, PR #52)*).
   *Amended 2026-11-25 (#5, PR #52): the input is Django's configured `DATABASES` (defaults applied),
   read through `asyncopation._django.connections.configured_databases()` (a structure copy; dict/list structure copied, leaves shared; ADR-0008
   Decision 6, ledger `BaseConnectionHandler.settings`). `configure()` is idempotent and callable
   again until a driver is open or opening (`RuntimeError` afterwards): the test runner renames `NAME` after
   `ready()`, so tests call it again once the test database exists; an alias whose configuration
   changed loses its server info. For PostgreSQL `build_config()` runs here (it reads the tz
   database), never inside the loop. An ENGINE without a driver (SQLite or PostGIS, for example) is
   recorded and raises `ImproperlyConfigured` at the first open / `setup()`, not in `ready()`, so
   projects that never open a driver keep starting. The app config `asyncopation.apps` is a
   top-level entry module above every layer and reaches `connection` only inside `ready()`.
   One alias never breaks `ready()`: any exception raised while preparing that alias (e.g. a
   `build_config` `ImproperlyConfigured`, a bad per-database `TIME_ZONE`, a missing driver library)
   is recorded for that alias and raised by its first open. Because the test
   runner changes `DATABASES` after `ready()`, an open compares the live configured settings with
   the configured snapshot and fails closed (`ImproperlyConfigured`: call
   `asyncopation.configure()` after the test databases are created) instead of reaching the
   original database.*
2. **Probe** (once per alias per process; server info is loop-independent): run Django's own
   server-info queries on an async connection - PostgreSQL `info.server_version` for `pg_version`;
   MySQL the exact `mysql_server_data` `SELECT` (version, sql_mode, isolation, lower-case names,
   `@@sql_auto_is_null`, the `CONVERT_TZ` zoneinfo test) - and inject the results into the
   compile-only wrapper's cached properties. Every derived property (`mysql_version`,
   `mysql_is_mariadb`, `sql_mode`, dependent features) follows from these. Django's own
   `connections[alias]` does not receive this data; the compile-only wrapper is the only recipient (ADR-0008).
   *Amended 2026-11-24 (#2, PR #46): ~~The frozen `Capabilities` (ADR-0003) are built here.~~ The
   probe no longer builds `Capabilities`: the driver builds them in `open()` and
   `driver.capabilities` is the single owner of runtime facts. PostgreSQL: the probe reads
   `server_version` / `pg_version` from `driver.capabilities` (taken from the startup parameters of
   `open()`'s direct connection, no query) and runs no second query. ~~MySQL/MariaDB: the future
   asyncmy driver's `open()` builds `Capabilities` from its own connection's server version
   before/while the probe runs, and the probe's `mysql_server_data` result must equal it (see
   Validation).~~ *(Amended 2026-11-25 (#7, PR #70): superseded below - the driver's `open()` runs
   `mysql_server_data` itself and builds `Capabilities` from the same result.)* The once-per-alias probe takes the first successfully opened driver's
   `driver.capabilities`; `server_version` is loop-independent, so later loops need no re-read. If
   the first open fails the probe stays once-unless-exception (retried on the next open).*
   *Amended 2026-11-25 (#7, PR #70): the MySQL/MariaDB probe does not run `mysql_server_data` itself. The asyncmy driver's `open()` runs it on its first direct connection (ADR-0003 Decision 2) and exposes the frozen result through the optional `SupportsServerData` protocol; the engine's `probe` reads `driver.server_data` only (`Capabilities.server_version` comes from the same `VERSION()` string, so there is nothing else to read) and builds `MySQLServerInfo` (all six fields, `sql_mode` as a string), so it stays pure and synchronous like PostgreSQL's. A driver without `SupportsServerData` makes the MySQL probe raise `TypeError`. The sentence above about the probe's `mysql_server_data` result equalling `Capabilities` is satisfied by construction (one query, one `VERSION()` string).*
   *Amended 2026-11-25 (#5, PR #52): the probe is a pure synchronous function of an open driver's
   `capabilities` (no I/O, no `await`), published with an atomic `dict.setdefault(alias, info)`;
   a loop that opens while the info is already stored injects that stored value, and loops that probe concurrently derive equal values from the same server, so the compile wrapper never sees a different one for long (`wrapper_for` replaces a wrapper whose info changed). Server
   info is process state: `close()` keeps it, `configure()` drops it for a changed alias. A probe
   or injection failure closes the driver just opened, leaves no open driver and stores no server info.*
3. **Open** (per `(alias, event loop)`): create the `AsyncDriver` and its pool inside that loop and
   open it. Triggered by `await asyncopation.setup()`.
   *Amended 2026-11-25 (#5, PR #52): `setup()` without arguments opens every alias that has a
   supported engine and skips the others with an INFO log, so a MySQL-only alias does not stop a
   PostgreSQL one; aliases named explicitly must be usable (otherwise `ImproperlyConfigured`), a
   single `str` is a `TypeError`. A failed or cancelled `setup()` closes only the drivers that call
   opened.*
   *Amended 2026-11-24 (#2, PR #46): `open()` first connects once directly, so bad credentials or
   `assume_role` fail immediately with the sync backend's Django exception and leave no pool
   behind; only then is the pool opened (the pool object is built earlier, without I/O, only to
   validate its sizes). The resulting order is open -> probe (reads
   `driver.capabilities`), no longer probe -> open.*
4. **Lazy fallback:** a query before `setup()` runs the same open/probe (*Amended 2026-11-24 (#2,
   PR #46): open first, then probe, as in Decision 3*) under an `asyncio.Lock`
   created inside that loop for that `(alias, loop)` entry, once-unless-exception, and logs a
   warning recommending the lifespan wrapper.
5. **Close** (`await asyncopation.close()`): stop new acquires, wait for in-flight statements up to a
   configurable timeout, then close the pools of the current loop and drop their registry entries.
   *Amended 2026-11-25 (#5, PR #52): `close(timeout)` (default `DEFAULT_CLOSE_TIMEOUT`, 10 s) marks the loop as closing (a per-loop counter, so overlapping closes are safe and each removes only the entries it began with) (`ensure_driver` raises
   `RuntimeError` meanwhile), waits on the per-`(alias, loop)` in-flight counter, closes the drivers
   (shielded from cancellation) even when the wait timed out, then raises `TimeoutError`; the
   marker is cleared afterwards so a restarted lifespan can reopen. An open in progress gets until
   the same `timeout` deadline (its driver is then closed); after that it is cancelled, so a hung
   connect cannot make shutdown outlast `timeout` (only an open is cancelled; a hung `driver.close()`
   is not bounded). A driver that fails to close is raised in preference to the `TimeoutError`
   (the timeout is logged). Only the current loop is touched.*
   *Amended 2026-11-25 (#7, PR #70): asyncmy's `Pool.wait_closed()` waits for every borrowed connection, so the asyncmy driver closes the pool, waits for it under a bound, then `terminate()`s it, which keeps the shielded driver close finite.*
   *Amended 2026-11-25 (#5, PR #61): the in-flight counter also counts every open pin block and
   open stream (ADR-0001 Decision 10), so `close()` waits for them up to its timeout and then closes
   anyway; pins still open then are marked closed first (the registry tracks them), so they raise
   `ConcurrentQueryError` instead of running on a closed pool; a pin whose connection arrives after
   that point (it was waiting in the pool) is marked closed on arrival.*
6. **Registry:** process-global, owned by `asyncopation.connection`, keyed by `(alias, loop)` for
   drivers/pools and by `alias` for server info. Never in a contextvar (the transient `compile_scope` swap of ADR-0008 uses asgiref's contextvar-backed storage but carries no state across an `await`), never populated at import.
   It exposes read-only pool stats per entry (in use / idle / waiting) for tests and metrics.
   *Amended 2026-11-25 (#7, PR #70): `waiting` means different things per driver: psycopg_pool counts requests queued for a connection; the asyncmy driver counts acquires in progress, which includes a task that is itself opening a new connection. `in_use` is `size - idle` for both, so it also counts connections still opening. Tests wait for the pool to settle; the `PoolStats` docstring says the same.*
7. **ASGI lifespan wrapper:** `asyncopation.connection.asgi.LifespanApp(django_asgi_app)` handles
   the `lifespan` scope (setup on startup, close on shutdown) and delegates `http` to Django.
   *Amended 2026-11-25 (#5, PR #52): the public surface is `LifespanApp(app)` only. It consumes
   the `lifespan` scope (startup calls `setup()` and reports `lifespan.startup.failed` on error;
   shutdown calls `close()` and reports `lifespan.shutdown.failed`, logging the exception) and
   never forwards it to the wrapped app, because Django's `ASGIHandler` cannot handle `lifespan`
   (it raises for any scope but `http`) so forwarding has no effect for the default use, and the
   default scope of `setup()` (aliases with a supported engine; Decision 3) removes the need for
   an `aliases` argument. Both can be added later without breaking callers. To combine the
   wrapper with other startup/shutdown work, call `asyncopation.setup()` / `asyncopation.close()`
   from your own lifespan handler.*

## Consequences
+ No sync I/O on the SQL generation path, by construction. + One place owns pools.
- Two open paths (explicit and lazy) must stay identical. - Per-loop entries can leak pools if a
loop is discarded without `close()`; tests assert the registry is empty after teardown.
  *Amended 2026-11-25 (#5, PR #52): the registry drops the entries of closed loops (their pools
  cannot be closed any more) with an ERROR log, so a dead loop never blocks `configure()`; test
  teardown still fails on such a leak.*
- Users must add `asyncopation` to `INSTALLED_APPS` and wrap their ASGI app.

## Implementation note
*Amended 2026-11-25 (#5, PR #52).* Pool sizing (ADR-0001 Decision 8: provisional `min_size=1`,
`max_size=10`) is decided by benchmark in #8, not in the implementation of this ADR. Modules:
`connection.registry` (drivers by `(alias, loop)`, server info by alias, read-only `PoolStats`,
in-flight counters), `connection.engines` (per-ENGINE `prepare` / `probe` / `inject`, imports psycopg
lazily so `import asyncopation` and MySQL-only installs load no driver), `connection.lifecycle`
(`configure`, the single open path `ensure_driver`, `setup`, `close`), `connection.asgi.LifespanApp`
(consumes the `lifespan` scope, never forwarding it to the wrapped app: startup -> `setup()`,
failure -> `lifespan.startup.failed`; shutdown -> `close()`; everything else is delegated), `asyncopation.apps`. `setup()` is all or nothing for the running loop (a failing alias closes the ones already opened);
`configure()` is rejected while a driver is open *or opening*; `close()` closes every driver even when
one close fails (the first error is raised afterwards). Registry bookkeeping assumes one thread per loop; starting
several loops in several threads before `setup()` is best effort (equal probe results make the
race benign).

*Amended 2026-11-24 (#2, PR #46).*
- **Capabilities ownership (PostgreSQL).** `open()` connects once directly, reads `server_version`
  from that connection's startup parameters (no query) and builds the frozen `Capabilities`;
  `driver.capabilities` is the single owner of runtime facts and is available only while the
  driver is open (`RuntimeError` before `open()` and after `close()`). The Decision 2 probe (#3,
  once per alias) takes `server_version` / `pg_version` from the first opened driver's
  `Capabilities` and must not run a second source of truth; the registry builds the alias-level
  server info from it. For MySQL/MariaDB the asyncmy driver's `open()` builds `Capabilities` from its own
  connection and the `mysql_server_data` probe result must equal it (Validation).
- **Fail-fast open.** The direct connection in `open()` happens before the pool is opened (the pool object is constructed first, with `open=False`, only to validate its sizes).
- **Pool options and `CONN_HEALTH_CHECKS`** (validation rule, defaults, the extra round trip per
  borrow) are specified in ADR-0001 Decision 8 (implementation note).

## Validation
- integration: no `SynchronousOnlyOperation` and no sync connection opened during `as_sql()` after
  `setup()` (plan Phase 1 checklist item); the differential suite also runs with Django's sync
  `connect` patched to raise.
- integration: concurrent first queries without `setup()` run the probe exactly once.
- integration: a second event loop gets its own pool; server info is probed once per process.
- e2e: uvicorn lifespan opens and closes pools; pool stats are zero after shutdown.
- *Amended 2026-11-24 (#2, PR #46):* integration (MySQL/MariaDB, asyncmy driver issue):
  `Capabilities.server_version` equals the version derived by the `mysql_server_data` probe.
  *Amended 2026-11-25 (#7, PR #70):* the probe result equals Django's sync `mysql_server_data` field by field (including with an `init_command` that changes `sql_mode`); `Engine.probe` for MySQL runs no query (a fake driver proves it); zero sync connections during `as_sql()` after `setup()` on MySQL and MariaDB; `driver.close()` with a leaked borrow finishes within its bound.
- *Amended 2026-11-25 (#5, PR #52):* integration: `configure()` after the test database exists reflects its
  name, and is rejected while a driver is open; a failed first open stores no server info and is
  retried; `close()` leaves no registry entry and zero pool stats; `import asyncopation` imports no
  database driver (subprocess test).
- *Amended 2026-11-25 (#5, PR #52):* integration: an open after `DATABASES` changed (the test runner's `NAME` rename) fails closed until `configure()` is called again; the pool is verified closed through `pg_stat_activity` (a unique `application_name`), not through the registry alone; `configure()` survives a bad alias and a missing driver library; a close waits for an open in progress and closes its driver.
