"""Test support for the connection lifecycle (ADR-0004), split by concern.

fakes (engine, driver, DATABASES), helpers (pure), logs, checks (teardown / leaks), loops (second
event loops), scenarios (open / close / setup interleavings), asgi, postgres (real engine).
"""
