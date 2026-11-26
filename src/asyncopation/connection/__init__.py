"""Layer 2 - connection ownership, driver registry, lifecycle and instrumentation hooks.

contextvars-based ownership instead of thread-locals (ADR-0001): borrow a pooled connection per
query outside transactions, pin one inside a transaction and reject use from other tasks. The
process-global registry keyed by ``(alias, loop)`` owns drivers, pools and server info (ADR-0004);
``hooks`` holds the query observers and interceptors (ADR-0005): ``observe()`` / ``intercept()``
scoped to a task tree, ``add_observer()`` / ``add_interceptor()`` (and their ``remove_*``)
process-wide, and ``QueueObserver`` for async consumers.

Importing this package imports no database driver: engines are wired lazily (``engines``).
"""

from asyncopation.connection.hooks import (
    QueryEvent,
    QueueObserver,
    add_interceptor,
    add_observer,
    intercept,
    observe,
    remove_interceptor,
    remove_observer,
)
from asyncopation.connection.lifecycle import close, configure, setup

__all__ = [
    "QueryEvent",
    "QueueObserver",
    "add_interceptor",
    "add_observer",
    "close",
    "configure",
    "intercept",
    "observe",
    "remove_interceptor",
    "remove_observer",
    "setup",
]  # lifecycle.ensure_driver stays internal (used by connection.executor)
