# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Phase 0: agentic workflow (Claude Code / Codex / Pi), guards, differential/e2e/benchmark harness.
- `asyncopation.drivers`: `AsyncDriver` / `AsyncDbConnection` protocols, frozen `Capabilities`, `ExecResult`, `PoolStats`, and the psycopg3 `PostgresDriver` (pool lifecycle, Django-compatible connection setup and error mapping, cancellation safety, `OPTIONS["server_side_binding"]`) (#2).
- `asyncopation.testing.RecordingDriver`: an `AsyncDriver` with queued results and a statement log for unit tests (#2).
