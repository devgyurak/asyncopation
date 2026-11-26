"""`aq(qs).alist()` under an observer: on the recording driver (unit) and on the live database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.support.differential import QuerySetFactory
from tests.support.engine.catalog import ROWS
from tests.support.engine.evaluate import CompileEnv, book_queryset
from tests.support.hooks.events import Recorder, hooks


@dataclass(frozen=True)
class ObservedAlist:
    recorder: Recorder
    driver: list[tuple[Any, Any]]
    """`(sql, params)` of every statement the driver received."""
    rows: int


async def observed_alist(env: CompileEnv, name: str) -> ObservedAlist:
    """`aq(<catalog case>).alist()` inside `observe(recorder, params=True)`."""
    from asyncopation import aq

    env.default.queue(ROWS.get(name, []))
    recorder = Recorder()
    with hooks().observe(recorder, params=True):
        rows = await aq(book_queryset(name)).alist()
    driver = [(statement.sql, statement.params) for statement in env.default.statements]
    return ObservedAlist(recorder, driver, len(rows))


@dataclass(frozen=True)
class LiveAlist:
    observed: list[tuple[str, tuple[Any, ...]]]
    compiled: list[tuple[str, tuple[Any, ...]]]
    rows: int


async def observed_live_alist(build: QuerySetFactory) -> LiveAlist:
    """`aq(build()).alist()` on the live database, observed; and what Django's own (sync)
    compiler makes of `build()`."""
    from asyncopation import aq

    recorder = Recorder()
    with hooks().observe(recorder, params=True):
        rows = await aq(build()).alist()
    sql, params = build().query.get_compiler(using="default").as_sql()
    observed = [(event.sql, tuple(event.params or ())) for event in recorder.started]
    return LiveAlist(observed, [(sql, tuple(params or ()))], len(rows))
