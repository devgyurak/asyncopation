"""Layer 2 - connection ownership, driver registry, lifecycle and instrumentation hooks.

contextvars-based ownership instead of thread-locals (ADR-0001): borrow a pooled connection per
query outside transactions, pin one inside a transaction and reject use from other tasks. The
process-global registry keyed by ``(alias, loop)`` owns drivers, pools and server info (ADR-0004)
and the observer/interceptor hooks (ADR-0005).

Importing this package imports no database driver: engines are wired lazily (``engines``).
"""

from asyncopation.connection.lifecycle import close, configure, setup

__all__ = [
    "close",
    "configure",
    "setup",
]  # lifecycle.ensure_driver stays internal (used by connection.executor)
