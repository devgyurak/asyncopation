"""The MySQL compile-only wrapper needs no mysqlclient and can never connect (ADR-0008)."""

import pytest

from asyncopation.compile.wrapper import CompileOnlyConnectionError
from tests.support.mysql_scripts import STATEMENTS, blocked, mysqldb_loaded
from tests.support.mysql_wrappers import make_mysql_wrapper

SCRIPTS = pytest.mark.parametrize("statement", list(STATEMENTS.values()), ids=list(STATEMENTS))


@SCRIPTS
def test_importing_and_using_the_wrapper_does_not_load_mysqldb(statement):
    # when
    result = mysqldb_loaded(statement)
    # then
    assert result == "0 False"


@SCRIPTS
def test_the_wrapper_works_while_mysqldb_and_mysql_base_cannot_be_imported(statement):
    # when
    result = blocked(statement)
    # then
    assert result == "0 False"


@pytest.mark.parametrize(
    "method",
    ["connect", "ensure_connection", "cursor", "chunked_cursor", "_cursor", "create_cursor"],
)
def test_the_wrapper_cannot_reach_a_database(alias, method):
    # given
    wrapper = make_mysql_wrapper(alias)
    # when / then
    with pytest.raises(CompileOnlyConnectionError):
        getattr(wrapper, method)()


def test_the_wrapper_cannot_open_a_connection(alias):
    # given
    wrapper = make_mysql_wrapper(alias)
    # when / then
    with pytest.raises(CompileOnlyConnectionError):
        wrapper.get_new_connection({})


def test_the_wrapper_is_a_mysql_vendor_wrapper_with_the_compiler_module_of_django(alias):
    # when
    wrapper = make_mysql_wrapper(alias)
    # then
    assert (wrapper.vendor, wrapper.ops.compiler_module) == (
        "mysql",
        "django.db.backends.mysql.compiler",
    )


def test_the_database_shim_binary_is_bytes(alias):
    # given
    wrapper = make_mysql_wrapper(alias)
    # when
    value = wrapper.Database.Binary(b"x")
    # then
    assert (value, type(value)) == (b"x", bytes)
