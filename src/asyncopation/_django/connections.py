"""The one place that swaps Django's per-context connection entry (ADR-0008, Decision 6).

``compile_scope`` needs ``connections[alias]`` to resolve to the compile-only wrapper while Django
generates SQL. This module is the only code in ``src/`` allowed to touch ``django.db.connections``,
and only through three operations: ``connections.__setitem__``, ``connections.__delitem__`` and a
read of the private ``connections._connections`` (to remember the previous wrapper without
creating a real one). Everything else - ``connections[alias]`` reads, ``.cursor()``,
``ensure_connection``, ``connect``, ``django.db.transaction`` - stays forbidden (guard A102).

The module exposes a single context manager, :func:`swapped`, which yields nothing: the previous
wrapper (possibly Django's real one) is never handed out by any name the guard lets other code use,
and Django's handler is bound only inside the factory, so ``asyncopation._django.connections`` has
no ``connections`` attribute.

The handler's storage is asgiref's thread-critical ``Local``: inside a running loop it is backed by
a contextvar, so a swap is visible to the current task and to tasks created afterwards, never to
siblings, the parent or other threads. The swap must therefore never span an ``await``, and a scope
must be exited in the ``contextvars`` Context that entered it. Exiting from another Context inside
a running loop would overwrite that Context's own swap, so it is detected and refused
(:class:`ScopeContextError`); without a running loop (plain threads) the entry is restored.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Any

__all__ = ["ScopeContextError", "swapped"]

_ABSENT: Any = object()


class ScopeContextError(RuntimeError):
    """A scope was exited from a different Context than the one that entered it."""


def _loop_is_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _build() -> Callable[[str, Any], AbstractContextManager[None]]:
    from django.db import connections

    entered: contextvars.ContextVar[bool] = contextvars.ContextVar("asyncopation_swap_entered")

    @contextmanager
    def swapped(alias: str, wrapper: Any) -> Generator[None]:
        """Make ``connections[alias]`` resolve to ``wrapper`` in the current context.

        Restores the previous entry (or deletes it when there was none) on exit, whatever
        happens inside; restoring never raises for an entry that already vanished. An exception
        from the body wins over :class:`ScopeContextError`, with one exception: a ``GeneratorExit``
        (the generator closed from another Context, whatever raised it) becomes the error.
        """
        previous = getattr(connections._connections, alias, _ABSENT)  # ty: ignore[missing-attribute]
        token = entered.set(True)
        failed = False
        try:
            connections[alias] = wrapper
            yield
        except GeneratorExit:
            # `close()` from another task is exactly the misuse to detect: do not count it as a
            # body error, so the Context check below can raise ScopeContextError instead
            raise
        except BaseException:
            failed = True
            raise
        finally:
            foreign = False
            try:
                entered.reset(token)
            except ValueError:
                foreign = True
            if foreign and _loop_is_running():
                if not failed:
                    raise ScopeContextError(
                        f"compile scope for {alias!r} exited from a different Context than the "
                        "one that entered it; the connection entry was left untouched"
                    )
            elif previous is _ABSENT:
                with suppress(AttributeError):
                    del connections[alias]
            else:
                connections[alias] = previous

    return swapped


swapped = _build()
del _build
