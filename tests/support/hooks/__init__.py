"""Support for the ADR-0005 observer tests (issue #4): recording observers and scenarios that run
statements, streams and transaction control under `observe()` / `add_observer()`.

The hooks module is imported lazily (`hooks()`), so a test that does not reach it still collects.
"""
