"""Environment for integration tests that call `aq(...).alist()` against the live test database."""

from __future__ import annotations

from tests.support.differential import DifferentialResult, QuerySetFactory, run_differential
from tests.support.evaluators import ASYNCOPATION


def prepare_registry() -> None:
    """Fresh configuration against the test database (earlier tests may have reset the registry)."""
    from tests.support.lifecycle.checks import configure_session

    configure_session()


async def release_drivers() -> None:
    """Close what `alist()` opened lazily and assert nothing leaked."""
    from tests.support.lifecycle.checks import teardown_lifecycle

    await teardown_lifecycle()


def asyncopation_runner():
    """`await run(build, op="list")` - sync ORM vs Asyncopation."""

    async def run(build: QuerySetFactory, op: str = "list") -> DifferentialResult:
        return await run_differential(build, ASYNCOPATION[op])

    return run
