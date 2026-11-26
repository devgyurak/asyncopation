"""Drive the sans-I/O cores without any runner: compile for real, answer `Execute` with fixed rows.

Control flow lives here so that the core tests are straight-line (ADR-0007 Decision 1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.db import connections
from django.db.models import Count, Sum, Value

from asyncopation._django import aggregation
from asyncopation.engine.compiled import CompiledQuery, ResultKind, compile_statement
from asyncopation.engine.runner import Compile, Convert, Core, Execute
from harness.catalog.models import Book
from tests.support.compile_wrappers import make_wrapper
from tests.support.connection_entry import has_entry


@dataclass
class Trace:
    """What a core asked for and what it returned."""

    requests: list[Any] = field(default_factory=list)
    compiled: list[CompiledQuery] = field(default_factory=list)
    result: Any = None

    @property
    def request_names(self) -> list[str]:
        return [type(request).__name__ for request in self.requests]

    @property
    def compile_kinds(self) -> list[ResultKind]:
        return [request.kind for request in self.requests if isinstance(request, Compile)]

    @property
    def executed(self) -> list[CompiledQuery]:
        return [request.compiled for request in self.requests if isinstance(request, Execute)]

    @property
    def sql(self) -> str:
        return self.compiled[0].sql


def drive(core: Core[Any], alias: str, row: tuple[Any, ...] | None = None) -> Trace:
    """Run `core`: `Compile` is answered by the real compile stage (PostgreSQL wrapper), `Execute`
    by `row` (the first row of a SINGLE statement; `None` for an EMPTY one, as `execute_single`)."""
    trace = Trace()
    wrapper = make_wrapper(alias)
    try:
        request = next(core)
        while True:
            trace.requests.append(request)
            if isinstance(request, Compile):
                answer: Any = compile_statement(
                    request.query,
                    alias,
                    wrapper,
                    autocommit=True,
                    kind=request.kind,
                    elide_empty=request.elide_empty,
                )
                trace.compiled.append(answer)
            elif isinstance(request, Convert):
                answer = aggregation.finish_aggregation(
                    request.compiled.compiler,
                    request.outer_query,
                    request.row,
                    request.empty_set_result,
                )
            else:
                answer = None if request.compiled.kind is ResultKind.EMPTY else row
            request = core.send(answer)
    except StopIteration as stop:
        trace.result = stop.value
    return trace


def book_query() -> Any:
    return Book.objects.all().query


def empty_book_query() -> Any:
    return Book.objects.filter(pk__in=[]).query


def count_exprs() -> dict[str, Any]:
    return {"n": Count("pk")}


def sum_exprs() -> dict[str, Any]:
    return {"total": Sum("price")}


def two_exprs() -> dict[str, Any]:
    return {"n": Count("pk"), "total": Sum("price")}


def plus_one_exprs() -> dict[str, Any]:
    """An aggregate expression whose empty-set value is unknown (`NotImplemented`): no eliding."""
    return {"total": Sum("price") + Value(1)}


def catching_core(query: Any) -> Core[str]:
    """A tiny core that handles the error of its `Execute` request itself."""
    compiled = yield Compile(query, ResultKind.SINGLE)
    try:
        yield Execute(compiled)
    except ValueError as exc:
        return f"caught {exc}"
    return "no error"


def compile_catching_core(query: Any) -> Core[str]:
    """A tiny core that handles an error of the compile stage itself."""
    try:
        yield Compile(query, ResultKind.SINGLE)
    except Exception as exc:
        return f"caught {type(exc).__name__}"
    return "no error"


def plain_core(query: Any) -> Core[CompiledQuery]:
    """A tiny core that does not handle errors."""
    compiled = yield Compile(query, ResultKind.SINGLE)
    yield Execute(compiled)
    return compiled


def sliced_book_query() -> Any:
    return Book.objects.all()[:5].query


def distinct_book_query() -> Any:
    return Book.objects.distinct().query


def annotated_book_query() -> Any:
    return Book.objects.annotate(n=Count("tags")).query


def outside_transaction_query() -> Any:
    """Compiling it in autocommit mode raises `TransactionManagementError` (`FOR UPDATE`)."""
    return Book.objects.select_for_update().query


def finalizing_core(query: Any, log: list[str]) -> Core[CompiledQuery]:
    """A tiny core with a `finally` that records that the generator was closed."""
    try:
        compiled = yield Compile(query, ResultKind.SINGLE)
        yield Execute(compiled)
    finally:
        log.append("closed")
    return compiled


def stubborn_core(query: Any, log: list[str]) -> Core[CompiledQuery]:
    """A misbehaving core: it ignores `GeneratorExit` and yields again, so `close()` raises
    `RuntimeError`."""
    compiled = yield Compile(query, ResultKind.SINGLE)
    try:
        yield Execute(compiled)
    except GeneratorExit:
        log.append("ignored GeneratorExit")
        yield Compile(query, ResultKind.SINGLE)
    return compiled


def raising_finally_core(query: Any, log: list[str], error: BaseException) -> Core[CompiledQuery]:
    """A core whose cleanup fails: its `finally` raises `error` while the run is closed."""
    compiled = yield Compile(query, ResultKind.SINGLE)
    try:
        yield Execute(compiled)
    finally:
        log.append("cleanup")
        raise error
    return compiled


def exception_recording_core(query: Any, log: list[str]) -> Core[CompiledQuery]:
    """A core that records the type of every exception that reaches it (and lets it go on)."""
    try:
        compiled = yield Compile(query, ResultKind.SINGLE)
        yield Execute(compiled)
    except BaseException as exc:
        log.append(type(exc).__name__)
        raise
    return compiled


def autocommit_reading_core(query: Any, alias: str) -> Core[tuple[bool, bool]]:
    """The autocommit flag Django code sees through `connections[alias]` before and after a
    `Compile` request: both stretches run inside the compile scope."""
    before = connections[alias].get_autocommit()
    yield Compile(query, ResultKind.SINGLE)
    return before, connections[alias].get_autocommit()


def scope_reading_handler_core(query: Any, alias: str) -> Core[bool | None]:
    """Whether the handler of an `Execute` error runs inside the compile scope (it must: the
    handler is Django code too, e.g. it may compile again)."""
    compiled = yield Compile(query, ResultKind.SINGLE)
    try:
        yield Execute(compiled)
    except ValueError:
        return has_entry(alias)
    return None


def scope_logging_core(query: Any, alias: str, log: list[bool]) -> Core[CompiledQuery]:
    """A core whose `finally` records whether the compile scope is open while it runs."""
    try:
        compiled = yield Compile(query, ResultKind.SINGLE)
        yield Execute(compiled)
    finally:
        log.append(has_entry(alias))
    return compiled


def multi_core(query: Any) -> Core[list[Any]]:
    """A tiny core that asks for every row of a MULTI statement."""
    compiled = yield Compile(query, ResultKind.MULTI)
    rows = yield Execute(compiled)
    return rows


def values_distinct_query() -> Any:
    """A `values()` query (it has a `selected` dict) that counts through a subquery."""
    return Book.objects.values("title").distinct().query


def values_distinct_sliced_query() -> Any:
    """`exists()` keeps the select clause of a distinct sliced query and annotates it in place."""
    return Book.objects.values("title").distinct().order_by("title")[:3].query


def values_sliced_query() -> Any:
    return Book.objects.values("title", "pages").order_by("-pages")[:3].query


def values_annotated_query() -> Any:
    return Book.objects.values("author").annotate(n=Count("pk")).query


def selected_after(core_function: Callable[[Any], Core[Any]], query: Any, alias: str) -> Any:
    """`(selected before, selected after)` of `query` around a drive of `core_function(query)`."""
    before = dict(query.selected)
    drive(core_function(query), alias, row=(1,))
    return before, dict(query.selected)
