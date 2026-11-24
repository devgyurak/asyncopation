# ADR-0004: Lifecycle, driver registry and server-info preload

- Status: Accepted
- Date: 2026-11-24
- Amended: 2026-11-24 (#2, PR #46) - Decision 2 (the driver, not the probe, builds `Capabilities`; PostgreSQL probe reads `driver.capabilities`; the once-per-alias probe takes the first successfully opened driver's capabilities; MySQL/MariaDB asyncmy driver builds them from its own connection and the probe result must equal it), Decision 3 (`open()` connects directly first: fail-fast, order open -> probe), Decision 4 (same order on the lazy path), new Validation item for MySQL/MariaDB; approved by: maintainer (decided to handle all review items in PR #46; to be confirmed in the PR review before merge); architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

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
   config/factory objects from `DATABASES`. No I/O, no asyncio objects.
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
   `open()`'s direct connection, no query) and runs no second query. MySQL/MariaDB: the future
   asyncmy driver's `open()` builds `Capabilities` from its own connection's server version
   before/while the probe runs, and the probe's `mysql_server_data` result must equal it (see
   Validation). The once-per-alias probe takes the first successfully opened driver's
   `driver.capabilities`; `server_version` is loop-independent, so later loops need no re-read. If
   the first open fails the probe stays once-unless-exception (retried on the next open).*
3. **Open** (per `(alias, event loop)`): create the `AsyncDriver` and its pool inside that loop and
   open it. Triggered by `await asyncopation.setup()`.
   *Amended 2026-11-24 (#2, PR #46): `open()` first connects once directly, so bad credentials or
   `assume_role` fail immediately with the sync backend's Django exception and leave no pool
   behind; only then is the pool created and opened. The resulting order is open -> probe (reads
   `driver.capabilities`), no longer probe -> open.*
4. **Lazy fallback:** a query before `setup()` runs the same open/probe (*Amended 2026-11-24 (#2,
   PR #46): open first, then probe, as in Decision 3*) under an `asyncio.Lock`
   created inside that loop for that `(alias, loop)` entry, once-unless-exception, and logs a
   warning recommending the lifespan wrapper.
5. **Close** (`await asyncopation.close()`): stop new acquires, wait for in-flight statements up to a
   configurable timeout, then close the pools of the current loop and drop their registry entries.
6. **Registry:** process-global, owned by `asyncopation.connection`, keyed by `(alias, loop)` for
   drivers/pools and by `alias` for server info. Never in a contextvar (the transient `compile_scope` swap of ADR-0008 uses asgiref's contextvar-backed storage but carries no state across an `await`), never populated at import.
   It exposes read-only pool stats per entry (in use / idle / waiting) for tests and metrics.
7. **ASGI lifespan wrapper:** `asyncopation.connection.asgi.LifespanApp(django_asgi_app)` handles
   the `lifespan` scope (setup on startup, close on shutdown) and delegates `http` to Django.

## Consequences
+ No sync I/O on the SQL generation path, by construction. + One place owns pools.
- Two open paths (explicit and lazy) must stay identical. - Per-loop entries can leak pools if a
loop is discarded without `close()`; tests assert the registry is empty after teardown.
- Users must add `asyncopation` to `INSTALLED_APPS` and wrap their ASGI app.

## Implementation note
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
