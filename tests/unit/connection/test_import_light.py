"""Importing the connection layer must not import any driver package (psycopg, asyncmy)."""

import pytest

from tests.support.lifecycle_scripts import (
    MYSQL_FAKE_OPEN,
    PSYCOPG_MISSING,
    PSYCOPG_MISSING_OPEN,
    asyncopation_modules_script,
    import_script,
    light_import_script,
    module_loaded_script,
    run_isolated,
)


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("import asyncopation.connection", id="connection"),
        pytest.param("import asyncopation.connection.engines", id="engines"),
        pytest.param("import asyncopation.connection.registry", id="registry"),
        pytest.param("import asyncopation.connection.asgi", id="asgi"),
        pytest.param("import asyncopation.connection.executor", id="executor"),
        pytest.param("import asyncopation.connection.lifecycle", id="lifecycle"),
        pytest.param("import asyncopation.connection.hooks", id="hooks"),
        pytest.param("import asyncopation; asyncopation.observe", id="lazy-observe"),
        pytest.param("import asyncopation; asyncopation.intercept", id="lazy-intercept"),
        pytest.param(
            "import asyncopation.connection as c; c.intercept; c.QueueObserver",
            id="interceptor-reexports",
        ),
        pytest.param("import asyncopation; asyncopation.setup; asyncopation.close", id="lazy-api"),
        pytest.param(
            "import asyncopation.connection as c; c.setup; c.close; c.configure", id="reexports"
        ),
    ],
)
def test_import_does_not_load_a_driver_package(statement):
    # when
    ran = run_isolated(import_script(statement))
    # then
    assert (ran.returncode, ran.stdout) == (0, "[]")


def test_opening_a_mysql_fake_engine_does_not_load_psycopg():
    # when
    ran = run_isolated(MYSQL_FAKE_OPEN)
    # then
    assert (ran.returncode, ran.stdout) == (0, "[]")


def test_configuring_a_postgresql_alias_without_psycopg_installed_does_not_fail():
    # when
    ran = run_isolated(PSYCOPG_MISSING)
    # then
    assert (ran.returncode, ran.stdout) == (0, "configured")


def test_opening_a_postgresql_alias_without_psycopg_raises_improperly_configured():
    # when
    ran = run_isolated(PSYCOPG_MISSING_OPEN)
    # then
    assert (ran.returncode, ran.stdout) == (0, "raised ImproperlyConfigured")


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("import asyncopation.errors", id="errors"),
        pytest.param("import asyncopation; asyncopation.ConcurrentQueryError", id="root-reexport"),
        pytest.param(
            "from asyncopation import ConcurrentQueryError, setup, close, configure", id="from-root"
        ),
        pytest.param(
            "import asyncopation; asyncopation.SyncEvaluationError", id="sync-evaluation-error"
        ),
        pytest.param("from asyncopation import SyncEvaluationError", id="from-root-sync-error"),
    ],
)
def test_import_does_not_load_django_or_a_driver_package(statement):
    # when
    ran = run_isolated(light_import_script(statement))
    # then
    assert (ran.returncode, ran.stdout) == (0, "[]")


def test_importing_the_package_loads_only_the_errors_module_of_asyncopation():
    # when
    ran = run_isolated(asyncopation_modules_script("import asyncopation"))
    # then
    assert (ran.returncode, ran.stdout) == (0, "['asyncopation', 'asyncopation.errors']")


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("from asyncopation import SyncEvaluationError", id="error-import"),
    ],
)
def test_the_query_facade_is_not_loaded_until_aq_is_accessed(statement):
    # when
    ran = run_isolated(module_loaded_script(statement, "asyncopation.query"))
    # then
    assert (ran.returncode, ran.stdout) == (0, "False")


@pytest.mark.parametrize("name", ["aq", "AsyncQuerySet"])
def test_accessing_a_lazy_export_loads_the_query_facade(name):
    # when
    ran = run_isolated(
        module_loaded_script(f"import asyncopation; asyncopation.{name}", "asyncopation.query")
    )
    # then
    assert (ran.returncode, ran.stdout) == (0, "True")


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("from asyncopation import ConcurrentQueryError", id="error-import"),
    ],
)
def test_the_hooks_module_is_not_loaded_until_observe_is_accessed(statement):
    # when
    ran = run_isolated(module_loaded_script(statement, "asyncopation.connection.hooks"))
    # then
    assert (ran.returncode, ran.stdout) == (0, "False")


def test_accessing_observe_on_the_package_loads_the_hooks_module():
    # when
    ran = run_isolated(
        module_loaded_script(
            "import asyncopation; asyncopation.observe", "asyncopation.connection.hooks"
        )
    )
    # then
    assert (ran.returncode, ran.stdout) == (0, "True")


def test_observe_is_listed_in_the_package_all_without_loading_the_hooks_module():
    # when
    ran = run_isolated(
        asyncopation_modules_script("import asyncopation; assert 'observe' in asyncopation.__all__")
    )
    # then
    assert (ran.returncode, ran.stdout) == (0, "['asyncopation', 'asyncopation.errors']")


def test_accessing_intercept_on_the_package_loads_the_hooks_module():
    # when
    ran = run_isolated(
        module_loaded_script(
            "import asyncopation; asyncopation.intercept", "asyncopation.connection.hooks"
        )
    )
    # then
    assert (ran.returncode, ran.stdout) == (0, "True")


def test_intercept_is_listed_in_the_package_all_without_loading_the_hooks_module():
    # when
    ran = run_isolated(
        asyncopation_modules_script(
            "import asyncopation; assert 'intercept' in asyncopation.__all__"
        )
    )
    # then
    assert (ran.returncode, ran.stdout) == (0, "['asyncopation', 'asyncopation.errors']")
