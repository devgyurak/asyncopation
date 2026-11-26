"""The sans-I/O protocol and its async runner (ADR-0007 Decision 1).

A core is a generator that yields requests and receives their results; it contains no ``await`` and
no I/O, so a recording driver or a sync runner can drive it. Three requests exist so far:
``Compile`` (a Django ``Query`` becomes a ``CompiledQuery``, synchronously inside ``compile_scope``),
``Execute`` (a ``CompiledQuery`` runs on the current executor) and ``Convert`` (the row of an
aggregation goes through the compiler's converters, which run user ``from_db_value`` code, so it is
served outside any ``compile_scope``, as ``materialize`` is: ADR-0008 Validation). An exception
from a request is thrown back into the core with ``generator.throw()``; a cancellation is not (the
run ends with ``close()``, which raises ``GeneratorExit`` in the core), so a core must not catch
``GeneratorExit`` (an ``except BaseException`` must re-raise). A ``finally`` may run cleanup, but
an error it raises never replaces the original exception: the runner attaches it to the original
as a note. ``Evaluate``, ``Batch``,
``Savepoint`` and ``SendSignal`` arrive with the ported cores that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

from asyncopation._django import aggregation
from asyncopation.compile.scope import compile_scope
from asyncopation.connection import lifecycle
from asyncopation.connection.executor import current_executor
from asyncopation.engine.compiled import CompiledQuery, ResultKind, compile_statement
from asyncopation.engine.execute import execute_multi, execute_single

__all__ = ["Compile", "Convert", "Core", "Execute", "Request", "run_core"]


@dataclass(frozen=True, slots=True)
class Compile:
    """Compile ``query`` (a Django ``Query``); the answer is a :class:`CompiledQuery`."""

    query: Any
    kind: ResultKind
    elide_empty: bool = True


@dataclass(frozen=True, slots=True)
class Execute:
    """Run ``compiled``; the answer is the rows (MULTI) or the first row or ``None`` (SINGLE)."""

    compiled: CompiledQuery


@dataclass(frozen=True, slots=True)
class Convert:
    """Convert the ``row`` of the aggregation ``outer_query`` that ``compiled`` ran; the answer is
    the ``{alias: value}`` dict (``empty_set_result`` stands for a missing row, as in Django)."""

    compiled: CompiledQuery
    outer_query: Any
    row: Any
    empty_set_result: list[Any]


@dataclass(frozen=True, slots=True)
class _Done:
    """The core returned ``value``: a ``StopIteration`` never crosses the ``compile_scope`` exit."""

    value: Any


type Request = Compile | Execute | Convert
type Core[T] = Generator[Request, Any, T]


def _describe(error: Exception) -> str:
    """``repr(error)``, or the type name when the ``repr`` itself fails: a note must never
    replace the original exception."""
    try:
        return repr(error)
    except Exception:
        return f"{type(error).__name__} (its repr failed)"


async def run_core[T](core: Core[T], alias: str) -> T:
    """Drive ``core`` against the current executor of ``alias`` and return its result.

    Every synchronous stretch of the core (between two requests) and every ``Compile`` run inside
    ``compile_scope``, so Django code the core calls (expression resolution) that reads
    ``connections[alias]`` sees the compile-only wrapper; the scope never spans an ``await``. The
    final ``close()`` runs inside the scope for failed or cancelled runs only (a core that
    returned or raised is finished); an error it raises is attached to the original exception as a note
    and never replaces it. ``Convert`` runs outside any scope: converters and user
    ``from_db_value`` code see the real connection registry, as in ``materialize``.
    """
    await lifecycle.ensure_driver(alias)  # the compile-only wrapper needs the alias's server info
    executor = current_executor(alias)
    wrapper = lifecycle.compile_wrapper(alias)
    autocommit = not executor.pinned

    def step(advance: Callable[..., Request], *args: Any) -> Request | _Done:
        with compile_scope(alias, wrapper, autocommit=autocommit):
            try:
                return advance(*args)
            except StopIteration as stop:
                return _Done(stop.value)

    try:
        request = step(next, core)
        while not isinstance(request, _Done):
            try:
                answer: Any
                if isinstance(request, Compile):
                    answer = compile_statement(
                        request.query,
                        alias,
                        wrapper,
                        autocommit=autocommit,
                        kind=request.kind,
                        elide_empty=request.elide_empty,
                    )
                elif isinstance(request, Convert):
                    answer = aggregation.finish_aggregation(
                        request.compiled.compiler,
                        request.outer_query,
                        request.row,
                        request.empty_set_result,
                    )
                elif request.compiled.requested is ResultKind.SINGLE:
                    answer = await execute_single(request.compiled, executor)
                else:
                    answer = await execute_multi(request.compiled, executor)
            except Exception as exc:
                request = step(core.throw, exc)
            else:
                request = step(core.send, answer)
        return request.value
    except BaseException as original:
        # A cancelled or failed run must not leave the generator suspended. An Exception raised
        # while closing never replaces the original (async-correctness rule 4): an error from
        # close() (a finally that raises, a core that ignores GeneratorExit) is attached to it as
        # a note, while a BaseException (a nested cancellation, KeyboardInterrupt) propagates.
        # Only close() is guarded: a ScopeContextError on entering or leaving the scope is a
        # programming error (the context state is corrupt), so it is never hidden behind the
        # original and propagates chained to it. A core that returned or raised is finished.
        with compile_scope(alias, wrapper, autocommit=autocommit):
            try:
                core.close()
            except Exception as close_error:
                original.add_note(f"closing the core also raised {_describe(close_error)}")
        raise
