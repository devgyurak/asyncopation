# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Phase 0: agentic workflow (Claude Code / Codex / Pi), guards, differential/e2e/benchmark harness.
- `asyncopation.drivers` (internal, experimental; not a stable API): `AsyncDriver` / `AsyncDbConnection` protocols and the frozen `Capabilities`, `ExecResult` and `PoolStats` value types (#2).
- PostgreSQL driver on psycopg3 (`PostgresDriver`) (#2):
  - Django-compatible connection setup (adapters template, `client_encoding`, isolation level, `TimeZone`, `assume_role`, `OPTIONS["server_side_binding"]`) and Django's error mapping.
  - Pool from `OPTIONS["pool"]`: `OPTIONS["pool"]` accepts `None`, `True`, `False` or any mapping of `psycopg_pool` `AsyncConnectionPool` keyword arguments (`min_size`, `max_size`, `timeout`, `max_lifetime`, ...). `None`, `True` and `False` all mean "use the defaults" (provisional `min_size=1`, `max_size=10` until the ADR-0004 implementation benchmark): the async side always pools, a deliberate divergence from Django, where `pool=False` means no pool, and the driver ignores `CONN_MAX_AGE`. Other types, unknown keys, any callable value, `close_returns=True` and the keys `name`, `configure`, `kwargs`, `conninfo` and `open` (the driver passes those itself) are rejected with `ImproperlyConfigured`; `OPTIONS["cursor_factory"]` (not a pool key) is still rejected by name. A size the user gives is never corrected (a missing `max_size` defaults to `max(10, min_size)`), so `min_size > max_size` is not fixed up: `psycopg_pool`'s builtin `ValueError` is raised from `open()` before any connection is made (an explicit exception to ADR-0003 Decision 4, which covers database errors; this is a configuration error). `CONN_HEALTH_CHECKS` enables the pool's `check_connection`.
  - `open()` connects directly first (bad credentials or `assume_role` fail immediately, no pool is left behind) and builds `Capabilities`, available only while the driver is open.
  - Cancellation safety: a cancelled statement is cancelled and rolled back; inside a transaction or with an open stream the connection is discarded. Streams still open at release are closed; a failed `COMMIT` restores autocommit.
  - A released connection wrapper, and its stale stream iterators, raise `InterfaceError`.
- `asyncopation.testing`: `RecordingDriver` (an `AsyncDriver` with queued results and a statement log, for unit tests; its connections raise `InterfaceError` after the borrow ends) and `NoQueuedResult`, `RecordingConnection`, `Statement`, with `Capabilities` and `ExecResult` re-exported (#2).
