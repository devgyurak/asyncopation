"""`evaluate_list` scenarios over a recording driver and a fake engine that injects a real
PostgreSQL compile-only wrapper (no database)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet
from django.db.models.query import ModelIterable

from asyncopation.connection.executor import pin
from asyncopation.engine.evaluate import evaluate_list
from tests.support.compile_wrappers import make_wrapper
from tests.support.engine.catalog import QUERYSETS, ROWS
from tests.support.engine.stages import compile_case
from tests.support.executors.fakes import ExecEnv
from tests.support.lifecycle.fakes import FAKE_ENGINE, FakeEnv, FakeInfo

ALIAS = "default"


class CompileEnv(ExecEnv):
    """`ExecEnv` whose engine injects a PostgreSQL compile-only wrapper and notes whether the
    driver was open when the wrapper was asked for."""

    def __init__(self, fake: FakeEnv) -> None:
        super().__init__(fake)
        self.opened_when_wrapper_asked: list[bool] = []


def install_compile_environment(fake: FakeEnv, monkeypatch: Any) -> CompileEnv:
    from asyncopation.connection import configure, engines
    from asyncopation.connection.engines import Engine

    env = CompileEnv(fake)

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        return lambda: env.drivers[alias]

    def probe(driver: Any) -> FakeInfo:
        return FakeInfo(driver.capabilities.server_version)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> Any:
        env.opened_when_wrapper_asked.append(env.drivers[alias].opened)
        return make_wrapper(alias)

    engine = Engine(prepare=prepare, probe=probe, inject=inject)
    monkeypatch.setitem(engines.FACTORIES, FAKE_ENGINE, lambda: engine)
    configure()
    return env


def book_queryset(name: str = "book_all") -> QuerySet[Any]:
    return QUERYSETS[name](ALIAS)


@dataclass(frozen=True)
class Evaluation:
    result: list[Any]
    cache: list[Any] | None
    statements: list[Any]
    borrows: int
    opened_before: bool
    wrapper_asked_while_open: bool

    @property
    def statement_tuples(self) -> list[tuple[Any, ...]]:
        return [tuple(statement) for statement in self.statements]

    @property
    def titles(self) -> list[str]:
        return [book.title for book in self.result]

    @property
    def cache_size(self) -> int:
        return len(self.cache or [])


async def evaluate(env: CompileEnv, name: str = "book_all", *, times: int = 1) -> Evaluation:
    """Evaluate the case `times` times (the rows are queued once) and report what happened."""
    env.default.queue(ROWS.get(name, []))
    queryset = book_queryset(name)
    opened_before = env.default.opened
    result: list[Any] = []
    for _ in range(times):
        result = await evaluate_list(queryset)
    return Evaluation(
        result,
        queryset._result_cache,
        env.default.statements,
        env.default.borrows,
        opened_before,
        bool(env.opened_when_wrapper_asked) and all(env.opened_when_wrapper_asked),
    )


def expected_statement(name: str) -> tuple[str, str, Any]:
    """The `fetch_all` statement (kind, SQL, params) the case compiles to."""
    compiled = compile_case(name, ALIAS)
    return ("fetch_all", compiled.sql, compiled.params)


async def evaluate_unsupported(env: CompileEnv) -> tuple[str, bool, list[Any], int]:
    """A QuerySet with a ModelIterable subclass: (error name, driver opened, statements, borrows)."""
    queryset = book_queryset()
    queryset._iterable_class = type("CustomIterable", (ModelIterable,), {})
    name = "none"
    try:
        await evaluate_list(queryset)
    except Exception as exc:
        name = type(exc).__name__
    return name, env.default.opened, env.default.statements, env.default.borrows


async def evaluate_in_pin(env: CompileEnv, name: str) -> list[Any]:
    """Evaluate inside a pinned connection (autocommit False for the compiler)."""
    env.default.queue(ROWS.get(name, []))
    async with pin(ALIAS):
        return await evaluate_list(book_queryset(name))


async def evaluate_outside_pin_error(env: CompileEnv, name: str) -> str:
    env.default.queue([])
    try:
        await evaluate_list(book_queryset(name))
    except Exception as exc:
        return type(exc).__name__
    return "none"
