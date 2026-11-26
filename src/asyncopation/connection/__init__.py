"""Layer 2 - connection ownership, driver registry, lifecycle and instrumentation hooks.

contextvars-based ownership instead of thread-locals (ADR-0001): borrow a pooled connection per
query outside transactions, pin one inside a transaction and reject use from other tasks. The
process-global registry keyed by ``(alias, loop)`` owns drivers, pools and server info (ADR-0004);
``hooks`` holds the query observers (ADR-0005): ``observe()`` scoped to a task tree,
``add_observer()`` / ``remove_observer()`` process-wide.

Importing this package imports no database driver: engines are wired lazily (``engines``).
"""

from asyncopation.connection.hooks import QueryEvent, add_observer, observe, remove_observer
from asyncopation.connection.lifecycle import close, configure, setup

__all__ = [
    "QueryEvent",
    "add_observer",
    "close",
    "configure",
    "observe",
    "remove_observer",
    "setup",
]  # lifecycle.ensure_driver stays internal (used by connection.executor)
