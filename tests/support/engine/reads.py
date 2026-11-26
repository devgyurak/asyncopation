"""Scenarios of the read operations (`aget`, `afirst`, `acount`, `aexists`, `alist`) over a
recording driver (no database); control flow lives here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet
from django.test import override_settings

from asyncopation import aq
from asyncopation.connection.executor import pin
from tests.support.aio import collect
from tests.support.compile_wrappers import make_wrapper
from tests.support.engine import routers
from tests.support.engine.catalog import BOOK_ROWS
from tests.support.engine.evaluate import ALIAS, CompileEnv

type Build = Callable[[], QuerySet[Any]]


@dataclass(frozen=True)
class Reading:
    result: Any
    error: BaseException | None
    sqls: list[str]
    borrows: int

    @property
    def error_type(self) -> str | None:
        return None if self.error is None else type(self.error).__name__

    @property
    def message(self) -> str | None:
        return None if self.error is None else str(self.error)


def _reading(env: CompileEnv, result: Any, error: BaseException | None) -> Reading:
    statements = env.default.statements
    return Reading(result, error, [s.sql or "" for s in statements], env.default.borrows)


async def _run(
    env: CompileEnv,
    op: str,
    build: Build,
    rows: Any,
    pinned: bool,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Reading:
    if rows is not None:
        env.default.queue(rows)
    wrapper = aq(build())
    result: Any = None
    error: BaseException | None = None
    try:
        if pinned:
            async with pin(ALIAS):
                result = await getattr(wrapper, op)(*args, **kwargs)
        else:
            result = await getattr(wrapper, op)(*args, **kwargs)
    except Exception as exc:
        error = exc
    return _reading(env, result, error)


async def read(
    env: CompileEnv, op: str, build: Build, rows: Any = None, *args: Any, **kwargs: Any
) -> Reading:
    """Run `aq(build()).<op>(*args, **kwargs)`; `rows` is queued first (when given)."""
    return await _run(env, op, build, rows, False, args, kwargs)


async def read_pinned(
    env: CompileEnv, op: str, build: Build, rows: Any = None, *args: Any, **kwargs: Any
) -> Reading:
    """Like `read`, inside a pinned connection (a transaction for the compiler)."""
    return await _run(env, op, build, rows, True, args, kwargs)


async def read_after_alist(env: CompileEnv, op: str, build: Build, rows: Any) -> Reading:
    """`alist()` fills the cache, then `<op>` runs on the same wrapper: the statements are those
    of the `alist()` alone when `<op>` is answered from the cache."""
    env.default.queue(rows)
    wrapper = aq(build())
    await wrapper.alist()
    result = await getattr(wrapper, op)()
    return _reading(env, result, None)


def book_rows(count: int) -> list[tuple[Any, ...]]:
    """`count` book rows (columns of `Book`), all the first catalog row."""
    return [BOOK_ROWS[0]] * count


def distinct_book_rows(count: int) -> list[tuple[Any, ...]]:
    """The first `count` rows of the catalog (distinct titles: alpha, beta, gamma)."""
    return BOOK_ROWS[:count]


def without_limit_support(monkeypatch: Any) -> None:
    """Make the shared compile-only wrapper report `supports_select_for_update_with_limit` False."""
    monkeypatch.setattr(
        make_wrapper(ALIAS).features, "supports_select_for_update_with_limit", False
    )


@dataclass(frozen=True)
class Iterated:
    """`async for` over a wrapper: the rows it yielded, whether the result cache got filled and
    the statements issued for the pass(es)."""

    rows: list[Any]
    cached: bool
    sqls: list[str]

    @property
    def titles(self) -> list[str]:
        return [row.title for row in self.rows]


async def iterate(
    env: CompileEnv, build: Build, rows: Any, *, after_alist: bool = False
) -> Iterated:
    """`async for` over `aq(build())` (after an `alist()` that filled the cache when asked)."""
    env.default.queue(rows)
    wrapper = aq(build())
    if after_alist:
        await wrapper.alist()
    yielded = await collect(wrapper)
    cached = wrapper.queryset._result_cache is not None
    return Iterated(yielded, cached, [s.sql or "" for s in env.default.statements])


async def router_asks(
    env: CompileEnv, op: str, build: Build, rows: Any, *, pinned: bool = False, **kwargs: Any
) -> int:
    """How many times the database router was asked during one `<op>()` (it answers `default`
    first and `other` afterwards, so a second ask would show as an unconfigured alias)."""
    with override_settings(DATABASE_ROUTERS=[routers.PATH]):
        routers.script(["default", "other"])
        await _run(env, op, build, rows, pinned, (), kwargs)
    return len(routers.ScriptedRouter.reads)
