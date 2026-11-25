"""Root fixtures: test markers by directory, committed seed data, differential runners.

Async code under test uses its own connections, which cannot see pytest-django's per-test
transaction. Data is therefore committed: `readonly_db` shares one seeded dataset per session,
`writable_db` re-seeds after each test.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from harness.seed import reset
from tests.support.differential import DifferentialResult, QuerySetFactory, run_differential
from tests.support.evaluators import ASYNCOPATION, REFERENCE

settings.register_profile(
    "default",
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.register_profile(
    "ci", parent=settings.get_profile("default"), max_examples=200, derandomize=True
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

_DIR_MARKERS = {"integration": pytest.mark.integration, "e2e": pytest.mark.e2e}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for part in Path(str(item.path)).parts:
            if part in _DIR_MARKERS:
                item.add_marker(_DIR_MARKERS[part])


@pytest.fixture(scope="session")
def django_db_setup(
    django_test_environment: None,
    django_db_blocker,
    django_db_keepdb: bool,
    django_db_createdb: bool,
    django_db_modify_db_settings: None,
) -> Iterator[None]:
    """Always create the default test DB.

    pytest-django only creates databases for tests marked `django_db`, and that marker wraps each
    test in a transaction the async code under test cannot see. We use committed data instead.
    """
    from django.test.utils import (
        setup_databases,
        teardown_databases,  # ty: ignore[missing-module-member] - missing from django-types
    )

    keepdb = django_db_keepdb and not django_db_createdb
    with django_db_blocker.unblock():
        config = setup_databases(verbosity=0, interactive=False, keepdb=keepdb, aliases={"default"})
    yield
    if not keepdb:
        with django_db_blocker.unblock():
            teardown_databases(config, verbosity=0)


@pytest.fixture(scope="session")
def lifecycle_configured(django_db_setup: None) -> Iterator[None]:
    """`configure()` again once the test database name is known (ADR-0004: `ready()` saw the
    original name); at session end no driver may be left in the registry."""
    from tests.support.lifecycle import assert_registry_empty_at_session_end, configure_session

    configure_session()
    yield
    assert_registry_empty_at_session_end()


@pytest.fixture(scope="session")
def seeded_db(django_db_setup: None, django_db_blocker) -> None:
    with django_db_blocker.unblock():
        reset()


@pytest.fixture
def readonly_db(seeded_db: None, django_db_blocker) -> Iterator[None]:
    """Committed seed data. Tests using it must not write."""
    with django_db_blocker.unblock():
        yield


@pytest.fixture
def writable_db(seeded_db: None, django_db_blocker) -> Iterator[None]:
    """Committed seed data, restored after the test."""
    with django_db_blocker.unblock():
        yield
        reset()


type Differential = Callable[..., Awaitable[DifferentialResult]]


@pytest.fixture
def differential() -> Differential:
    """`await differential(build, op="list")` - sync ORM vs Asyncopation."""

    async def run(build: QuerySetFactory, op: str = "list") -> DifferentialResult:
        return await run_differential(build, ASYNCOPATION[op])

    return run


@pytest.fixture
def reference_differential() -> Differential:
    """`await reference_differential(build, op="list")` - sync ORM vs Django core async (self-check)."""

    async def run(build: QuerySetFactory, op: str = "list") -> DifferentialResult:
        return await run_differential(build, REFERENCE[op])

    return run
