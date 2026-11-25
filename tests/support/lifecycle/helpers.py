"""Small pure helpers that keep control flow out of the test modules."""

from __future__ import annotations

from typing import Any

from tests.support.lifecycle.fakes import Recorder


def entry_aliases() -> list[str]:
    from asyncopation.connection import registry

    return sorted(alias for alias, _loop in registry.entries())


def distinct(objects: list[Any]) -> int:
    return len({id(o) for o in objects})


def configured_database_names(recorder: Recorder) -> list[str]:
    return [p.name for p in recorder.prepares]


def pg_version_number(server_version: tuple[int, ...]) -> int:
    return server_version[0] * 10000 + server_version[1]


def aliases_of(entries: list[tuple[str, Any]]) -> list[str]:
    """The alias of every registry entry, in order."""
    return [alias for alias, _loop in entries]


def prepared_aliases(recorder: Recorder) -> list[str]:
    return [p.alias for p in recorder.prepares]


def prepared_names(recorder: Recorder) -> list[str]:
    return [p.name for p in recorder.prepares]


def errors_in(results: list[Any]) -> list[BaseException]:
    return [r for r in results if isinstance(r, BaseException)]


def drivers_in(results: list[Any]) -> list[Any]:
    return [r for r in results if not isinstance(r, BaseException)]


def last_prepared_name(recorder: Recorder, alias: str = "default") -> str:
    return recorder.last_prepare(alias).name


def error_names(results: list[Any]) -> list[str]:
    """The class name of every raised error in `results`, `ok` for a value."""
    return [type(r).__name__ if isinstance(r, BaseException) else "ok" for r in results]


def injected_aliases(recorder: Recorder) -> list[str]:
    return [alias for alias, _info in recorder.injects]


async def failing(coroutine: Any, expected: type[BaseException]) -> BaseException:
    """Await `coroutine`, which must raise `expected`; return the error (for setup steps)."""
    try:
        await coroutine
    except expected as exc:
        return exc
    raise AssertionError(f"expected {expected.__name__}, but nothing was raised")
