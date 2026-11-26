"""`execute_single` over a fake executor: compile a catalog case as a SINGLE statement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asyncopation.engine.compiled import CompiledQuery, ResultKind, compile_statement
from asyncopation.engine.execute import execute_single
from tests.support.compile_wrappers import make_wrapper
from tests.support.engine.catalog import QUERYSETS
from tests.support.engine.stages import FakeExecutor


@dataclass(frozen=True)
class SingleExecuted:
    compiled: CompiledQuery
    fetched: list[tuple[str, Any]]
    result: tuple[Any, ...] | None


async def execute_single_case(name: str, alias: str, rows: list[tuple[Any, ...]]) -> SingleExecuted:
    queryset = QUERYSETS[name](alias)
    compiled = compile_statement(
        queryset.query, alias, make_wrapper(alias), autocommit=True, kind=ResultKind.SINGLE
    )
    executor = FakeExecutor(rows)
    result = await execute_single(compiled, executor)  # ty: ignore[invalid-argument-type]
    return SingleExecuted(compiled, executor.calls, result)
