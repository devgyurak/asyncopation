"""One question, three answers: Django's own `Query` method, the core on a sync runner and the
core on the async runner (ADR-0007 Validation). Values are compared with their types, and so are
the SQL and parameters of the statements that reached the database."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.db import connections
from django.test.utils import CaptureQueriesContext

from asyncopation.engine.compiled import ResultKind
from asyncopation.engine.cores import aggregation_core, count_core, exists_core
from asyncopation.engine.runner import Core, Execute, run_core
from tests.support.core_cases import (
    AGGREGATES,
    ANNOTATION_AGGREGATES,
    COMPOSITE_PK_AGGREGATES,
    INVALID_ALIAS_AGGREGATES,
    POSTGRESQL_ANNOTATION_AGGREGATES,
    POSTGRESQL_QUERIES,
    QUERIES,
    QUERIES_FOR_AGGREGATES,
    ZOO_AGGREGATES,
    composite_pk_query,
    zoo_query,
)
from tests.support.seeded import in_sync
from tests.support.sync_runner import run_core_sync

ALIAS = "default"


def typed(value: Any) -> Any:
    """`value` with the type names of itself (and of the entries of a dict), for strict equality."""
    if isinstance(value, dict):
        return ("dict", tuple((key, typed(item)) for key, item in value.items()))
    return (type(value).__name__, value)


def captured(function: Callable[[], Any]) -> Any:
    try:
        return typed(function())
    except Exception as exc:
        return ("error", type(exc).__name__, str(exc))


async def captured_async(function: Callable[[], Any]) -> Any:
    try:
        return typed(await function())
    except Exception as exc:
        return ("error", type(exc).__name__, str(exc))


type Statements = list[tuple[str, tuple[Any, ...]]]


def traced[T](core: Core[T], log: Statements) -> Core[T]:
    """`core`, with the SQL and parameters of every statement it executes appended to `log`."""
    try:
        request = next(core)
        while True:
            if isinstance(request, Execute) and request.compiled.kind is not ResultKind.EMPTY:
                log.append((request.compiled.sql, request.compiled.params))
            try:
                answer = yield request
            except Exception as exc:
                request = core.throw(exc)
            else:
                request = core.send(answer)
    except StopIteration as stop:
        return stop.value


def captured_sql(function: Callable[[], Any]) -> tuple[Any, list[str]]:
    """The outcome of `function` and the SQL Django's connection logged while it ran."""
    with CaptureQueriesContext(connections[ALIAS]) as queries:
        outcome = captured(function)
    return outcome, [query["sql"] for query in queries.captured_queries]


@dataclass(frozen=True)
class Trio:
    django: Any
    sync_runner: Any
    async_runner: Any
    django_sql: list[str] = field(default_factory=list)
    sync_sql: list[str] = field(default_factory=list)
    sync_statements: Statements = field(default_factory=list)
    async_statements: Statements = field(default_factory=list)

    @property
    def runners_match_django(self) -> bool:
        return self.sync_runner == self.django and self.async_runner == self.django

    @property
    def statements_match_django(self) -> bool:
        """The same SQL and parameters reach the database: Django's own method (as its connection
        logged it), the core on the sync runner (logged the same way), and the core on the async
        runner against the sync runner (the compile-only wrapper against the real connection)."""
        return (
            self.django_sql == self.sync_sql
            and self.sync_statements == self.async_statements
            and len(self.django_sql) == len(self.async_statements)
        )

    @property
    def matches_django(self) -> bool:
        """Django's method succeeded and the cores answered the same, with the same statements."""
        return not self.failed and self.runners_match_django and self.statements_match_django

    @property
    def agrees_with_django(self) -> bool:
        """Values (or errors) and statements match, whether or not Django's method succeeded."""
        return self.runners_match_django and self.statements_match_django

    @property
    def async_type_name(self) -> str | None:
        """The Python type name of the first aggregate the async runner returned, else ``None``
        (the runner failed: an error outcome is not shaped like a value)."""
        try:
            return self.async_runner[1][0][1][0]
        except (IndexError, KeyError, TypeError):
            return None

    @property
    def failed(self) -> bool:
        return self.django[0] == "error"


async def _trio(django_call: Callable[[], Any], make_core: Callable[[], Core[Any]]) -> Trio:
    django, django_sql = await in_sync(lambda: captured_sql(django_call))
    sync_log: Statements = []
    sync_runner, sync_sql = await in_sync(
        lambda: captured_sql(lambda: run_core_sync(traced(make_core(), sync_log), ALIAS))
    )
    async_log: Statements = []
    async_runner = await captured_async(lambda: run_core(traced(make_core(), async_log), ALIAS))
    return Trio(django, sync_runner, async_runner, django_sql, sync_sql, sync_log, async_log)


async def count_trio(build: Callable[[], Any]) -> Trio:
    return await _trio(lambda: build().get_count(ALIAS), lambda: count_core(build()))


async def exists_trio(build: Callable[[], Any]) -> Trio:
    return await _trio(lambda: build().has_results(ALIAS), lambda: exists_core(build()))


async def aggregation_trio(build: Callable[[], Any], exprs: Callable[[], dict[str, Any]]) -> Trio:
    return await _trio(
        lambda: build().get_aggregation(ALIAS, exprs()), lambda: aggregation_core(build(), exprs())
    )


async def named_count_trio(name: str) -> Trio:
    return await count_trio(QUERIES[name])


async def named_exists_trio(name: str) -> Trio:
    return await exists_trio(QUERIES[name])


async def named_aggregation_trio(query_name: str, aggregate_name: str) -> Trio:
    return await aggregation_trio(QUERIES_FOR_AGGREGATES[query_name], AGGREGATES[aggregate_name])


async def zoo_aggregation_trio(aggregate_name: str) -> Trio:
    return await aggregation_trio(zoo_query, ZOO_AGGREGATES[aggregate_name])


async def postgresql_count_trio(name: str) -> Trio:
    return await count_trio(POSTGRESQL_QUERIES[name])


async def postgresql_exists_trio(name: str) -> Trio:
    return await exists_trio(POSTGRESQL_QUERIES[name])


async def annotation_aggregation_trio(name: str) -> Trio:
    build, exprs = ANNOTATION_AGGREGATES[name]
    return await aggregation_trio(build, exprs)


async def postgresql_annotation_aggregation_trio(name: str) -> Trio:
    build, exprs = POSTGRESQL_ANNOTATION_AGGREGATES[name]
    return await aggregation_trio(build, exprs)


async def composite_pk_aggregation_trio(name: str) -> Trio:
    return await aggregation_trio(composite_pk_query, COMPOSITE_PK_AGGREGATES[name])


async def invalid_alias_aggregation_trio(name: str) -> Trio:
    return await aggregation_trio(QUERIES["all"], INVALID_ALIAS_AGGREGATES[name])
