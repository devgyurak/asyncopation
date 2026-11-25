"""Public exceptions that asyncopation raises itself (not mapped driver errors: see ``drivers``).

This module imports nothing, so ``import asyncopation`` can expose these names without loading
Django or a database driver (an import-linter contract keeps it that way).
"""

from __future__ import annotations

__all__ = ["ConcurrentQueryError", "SyncEvaluationError"]


class ConcurrentQueryError(RuntimeError):
    """A connection pinned to one task was used in a way that would corrupt it (ADR-0001).

    Raised for use from a task other than the owner, re-entrant use while a statement or stream is
    in flight, and any use after the pinned block exited. A ``RuntimeError``, not a ``DatabaseError``:
    retry loops that catch database errors must not swallow a programmer error.
    """


class SyncEvaluationError(RuntimeError):
    """A synchronous QuerySet evaluator was called on an ``aq()`` wrapper (ADR-0006).

    ``list(aq(qs))``, ``aq(qs).get()``, ``len(aq(qs))`` and the other synchronous evaluators would
    run blocking database I/O, so the wrapper refuses them and names the ``a*`` method to await.
    """
