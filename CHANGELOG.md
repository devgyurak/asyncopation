# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Phase 0: agentic workflow (Claude Code / Codex / Pi), guards, differential/e2e/benchmark harness.
- `asyncopation.drivers`: `AsyncDriver` / `AsyncDbConnection` protocols, frozen `Capabilities`, `ExecResult`, `PoolStats`, and the psycopg3 `PostgresDriver` (pool lifecycle, Django-compatible connection setup and error mapping, cancellation safety, `OPTIONS["server_side_binding"]`) (#2). Review follow-ups: pool options are validated (`DATABASES[alias]["OPTIONS"]["pool"]`; `name`, sync callables and `cursor_factory` raise `ImproperlyConfigured`; `CONN_HEALTH_CHECKS` enables the pool's `check_connection`), `open()` connects directly first so bad credentials or `assume_role` fail immediately and leave no pool behind, a statement cancelled inside a transaction or an open stream discards the connection, and a released connection wrapper raises `InterfaceError`. The driver owns `Capabilities` (`server_version` from the connection's startup parameters).
- `asyncopation.testing.RecordingDriver`: an `AsyncDriver` with queued results and a statement log for unit tests, conformant to the driver protocol; `Capabilities` and `ExecResult` are re-exported from `asyncopation.testing` (#2).
