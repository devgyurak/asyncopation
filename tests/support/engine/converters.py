"""Probe fields for the conversion stage: where do converters and `from_db_value` run?

`from_db_value` is user code. It must see Django's real connection registry, never the
compile-only wrapper: the conversion runs outside `compile_scope` (ADR-0008 Validation).
"""

from __future__ import annotations

from typing import Any

from django.db.models import IntegerField, Max, Value

from asyncopation.engine.cores import aggregation_core
from asyncopation.engine.evaluate import evaluate_list
from asyncopation.engine.runner import run_core
from harness.catalog.models import Book
from tests.support.connection_entry import has_entry
from tests.support.engine.evaluate import ALIAS, CompileEnv

SEEN: list[bool] = []


class ScopeProbeField(IntegerField):
    """Records whether a Django connection entry (the compile scope) exists while it converts."""

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        SEEN.append(has_entry(ALIAS))
        return value


class RaisingField(IntegerField):
    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        raise ValueError("conversion failed")


async def alist_conversion_scope(env: CompileEnv) -> list[bool]:
    """What `ScopeProbeField.from_db_value` saw during `evaluate_list`."""
    SEEN.clear()
    env.default.queue([(1,), (2,)])
    queryset = Book.objects.annotate(p=Value(1, output_field=ScopeProbeField())).values_list("p")
    await evaluate_list(queryset)
    return list(SEEN)


async def aggregation_conversion_scope(env: CompileEnv) -> list[bool]:
    """What `ScopeProbeField.from_db_value` saw during an `aggregation_core` run."""
    SEEN.clear()
    env.default.queue([(5,)])
    exprs = {"m": Max("pages", output_field=ScopeProbeField())}
    await run_core(aggregation_core(Book.objects.all().query, exprs), ALIAS)
    return list(SEEN)


async def aggregation_conversion_error(env: CompileEnv) -> BaseException | None:
    """The error of an aggregation whose `from_db_value` raises."""
    env.default.queue([(5,)])
    exprs = {"m": Max("pages", output_field=RaisingField())}
    try:
        await run_core(aggregation_core(Book.objects.all().query, exprs), ALIAS)
    except Exception as exc:
        return exc
    return None
