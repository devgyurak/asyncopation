"""Runners for the engine stages without a database: compile, execute, materialize, and the
reference (Django's own iterable fed the same fixed rows). Control flow lives here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest import mock

from django.db.models.sql.compiler import SQLCompiler

from asyncopation.compile.scope import compile_scope
from asyncopation.engine.compiled import CompiledQuery, compile_query
from asyncopation.engine.execute import execute_multi
from asyncopation.engine.materialize import materialize
from tests.support.compile_wrappers import make_wrapper
from tests.support.engine.catalog import QUERYSETS, ROWS
from tests.support.snapshot import Snapshot, snapshot_result


def compile_case(name: str, alias: str, *, autocommit: bool = True) -> CompiledQuery:
    return compile_query(
        QUERYSETS[name](alias), make_wrapper(alias), alias=alias, autocommit=autocommit
    )


def materialized(name: str, alias: str) -> list[Any]:
    """What `materialize` builds from the case's fixed rows."""
    queryset = QUERYSETS[name](alias)
    compiled = compile_query(queryset, make_wrapper(alias), alias=alias, autocommit=True)
    rows = ROWS[name]
    assert all(len(row) == compiled.col_count for row in rows), "fixed rows do not fit the SELECT"
    return materialize(queryset, compiled, rows)


def django_materialized(name: str, alias: str) -> list[Any]:
    """What Django's own iterable builds from the same rows: `execute_sql` returns them (after the
    `as_sql` it would run), everything after that is Django's code."""
    queryset = QUERYSETS[name](alias)
    rows = ROWS[name]

    def execute_sql(self: SQLCompiler, *args: Any, **kwargs: Any) -> list[Any]:
        self.as_sql()
        return [rows]

    with (
        compile_scope(alias, make_wrapper(alias), autocommit=True),
        mock.patch.object(SQLCompiler, "execute_sql", execute_sql),
    ):
        return list(queryset)


def snapshots(name: str, alias: str) -> tuple[Snapshot, Snapshot]:
    """(ours, Django's) comparable snapshots of the case."""
    return (
        snapshot_result(materialized(name, alias)),
        snapshot_result(django_materialized(name, alias)),
    )


def param_values(compiled: CompiledQuery) -> tuple[Any, ...]:
    """The compiled params with the driver's adapter wrappers (psycopg `Int4`...) unwrapped."""
    return tuple(getattr(param, "obj", param) for param in compiled.params)


def attribute_values(result: list[Any], attribute: str) -> list[Any]:
    return [getattr(obj, attribute) for obj in result]


def cached_related(result: list[Any], name: str) -> list[Any]:
    """The related object cached on each instance under `name` (None when nothing is cached)."""
    return [obj._state.fields_cache.get(name) for obj in result]


def none_flags(values: list[Any]) -> list[bool]:
    return [value is None for value in values]


def same_object_flags(values: list[Any], expected: Any) -> list[bool]:
    return [value is expected for value in values]


def peer_shape(result: list[Any]) -> tuple[int, int, bool]:
    """(distinct peer lists, length of the first, whether it holds exactly these instances)."""
    lists = [getattr(obj._state, "peers", None) for obj in result]
    first = lists[0] or []
    return (
        len({id(peers) for peers in lists}),
        len(first),
        [ref() for ref in first] == result,
    )


def access_error(result: list[Any], attribute: str) -> str:
    """Class name of the error that reading `attribute` of the first instance raises, or "none"."""
    try:
        getattr(result[0], attribute)
    except Exception as exc:
        return type(exc).__name__
    return "none"


def as_sql_calls(name: str, alias: str) -> int:
    """How many times `compile_query` calls `SQLCompiler.as_sql`."""
    original = SQLCompiler.as_sql
    calls: list[None] = []

    def counting(self: SQLCompiler, *args: Any, **kwargs: Any) -> Any:
        calls.append(None)
        return original(self, *args, **kwargs)

    with mock.patch.object(SQLCompiler, "as_sql", counting):
        compile_case(name, alias)
    return len(calls)


class FakeExecutor:
    """Records `fetch_all` calls and returns the rows it was built with."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Any]] = []

    async def fetch_all(self, sql: str, params: Any) -> list[tuple[Any, ...]]:
        self.calls.append((sql, params))
        return self.rows


@dataclass(frozen=True)
class Executed:
    compiled: CompiledQuery
    rows: list[tuple[Any, ...]]
    fetched: list[tuple[str, Any]]
    result: list[tuple[Any, ...]]


async def execute_case(name: str, alias: str, rows: list[tuple[Any, ...]]) -> Executed:
    compiled = compile_case(name, alias)
    executor = FakeExecutor(rows)
    result = await execute_multi(compiled, executor)  # ty: ignore[invalid-argument-type]
    return Executed(compiled, rows, executor.calls, result)
