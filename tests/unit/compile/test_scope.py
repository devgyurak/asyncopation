"""compile_scope and the compile-only wrapper (ADR-0008, Decisions 1-3)."""

import asyncio

import pytest

from asyncopation.compile.postgresql import PostgreSQLCompileWrapper
from asyncopation.compile.scope import compile_scope
from asyncopation.compile.server_info import ServerInfo
from asyncopation.compile.wrapper import AUTOCOMMIT, CompileOnlyConnectionError, CompileOnlyMixin
from tests.support import connection_entry as connections
from tests.support.compile_scope import (
    autocommit_after_failing_scope,
    autocommit_in_scope,
    call_inside_scope,
    failing_scope,
    nested_autocommit,
    nested_peeks,
    peek_inside_scope,
    removed_entry_then_raise,
)
from tests.support.compile_wrappers import (
    make_distinct_wrapper,
    make_wrapper,
    pg_info,
)
from tests.support.connect_counter import (
    construct_while_connecting_is_forbidden,
    swallowed_attempts,
)
from tests.support.driver_imports import imported_driver_modules


def test_scope_restores_the_previous_wrapper(alias):
    # given
    previous, scoped = make_distinct_wrapper(alias), make_wrapper(alias)
    connections.install(alias, previous)
    # when
    inside = peek_inside_scope(alias, scoped)
    # then
    assert (inside, connections.peek(alias)) == (scoped, previous)


def test_scope_removes_the_entry_when_there_was_none(alias):
    # given
    scoped = make_wrapper(alias)
    # when
    inside = peek_inside_scope(alias, scoped)
    # then
    assert (inside, connections.peek(alias), connections.has_entry(alias)) == (scoped, None, False)


EXCEPTIONS = pytest.mark.parametrize(
    "exception", [ValueError, asyncio.CancelledError, KeyboardInterrupt]
)


@EXCEPTIONS
def test_scope_restores_the_previous_wrapper_when_the_body_raises(alias, exception):
    # given
    previous, scoped = make_distinct_wrapper(alias), make_wrapper(alias)
    connections.install(alias, previous)
    # when
    caught, after = failing_scope(alias, scoped, exception)
    # then
    assert (caught, after) == (exception.__name__, previous)


@EXCEPTIONS
def test_scope_removes_the_entry_when_the_body_raises_and_there_was_none(alias, exception):
    # when
    caught, after = failing_scope(alias, make_wrapper(alias), exception)
    # then
    assert (caught, after) == (exception.__name__, None)


def test_nested_scopes_restore_stack_wise(alias):
    # given
    outer, inner = make_distinct_wrapper(alias), make_wrapper(alias)
    # when
    seen = nested_peeks(alias, outer, inner)
    # then
    assert seen == [outer, inner, outer, None]


def test_nesting_the_same_wrapper_restores_its_autocommit(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    seen = nested_autocommit(alias, wrapper)
    # then
    assert seen == [False, True, False, True]


@pytest.mark.parametrize("autocommit", [True, False])
def test_get_autocommit_returns_the_scope_argument(alias, autocommit):
    # given
    wrapper = make_wrapper(alias)
    # when
    value = autocommit_in_scope(alias, wrapper, autocommit)
    # then
    assert value is autocommit


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("connect", ()),
        ("ensure_connection", ()),
        ("_cursor", ()),
        ("cursor", ()),
        ("chunked_cursor", ()),
        ("create_cursor", ()),
        ("get_new_connection", ({},)),
    ],
)
def test_connection_methods_raise_compile_only_error(alias, method, args):
    # given
    wrapper = make_wrapper(alias)
    # when / then
    with pytest.raises(CompileOnlyConnectionError, match="compile-only"):
        getattr(wrapper, method)(*args)


@pytest.mark.parametrize(
    "method", ["connect", "ensure_connection", "_cursor", "cursor", "chunked_cursor"]
)
def test_connection_methods_raise_inside_a_scope_too(alias, method):
    # given
    wrapper = make_wrapper(alias)
    # when / then
    with pytest.raises(CompileOnlyConnectionError, match="compile-only"):
        call_inside_scope(alias, wrapper, method)


def test_get_autocommit_outside_a_scope_is_true(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    value = wrapper.get_autocommit()
    # then
    assert value is True


def test_compile_only_connection_error_is_a_runtime_error():
    # when
    is_runtime_error = issubclass(CompileOnlyConnectionError, RuntimeError)
    # then
    assert is_runtime_error


def test_mixin_comes_first_in_the_mro():
    # when
    mro = PostgreSQLCompileWrapper.__mro__
    # then
    assert mro[:2] == (PostgreSQLCompileWrapper, CompileOnlyMixin)


def test_base_mixin_apply_server_info_is_abstract():
    # when / then
    with pytest.raises(NotImplementedError):
        CompileOnlyMixin().apply_server_info(pg_info())


def test_construction_makes_no_connection(alias):
    # when
    wrapper, attempts = construct_while_connecting_is_forbidden(alias)
    # then
    assert (isinstance(wrapper, PostgreSQLCompileWrapper), attempts) == (True, 0)


def test_injected_pg_version_is_served_without_connecting(alias):
    # given
    wrapper = make_wrapper(alias, version=160004)
    # when
    version = wrapper.pg_version
    # then
    assert version == 160004


@pytest.mark.parametrize("module", ["asyncopation.compile", "asyncopation.compile.scope"])
def test_importing_the_compile_layer_does_not_import_a_driver(module):
    # when
    loaded = imported_driver_modules(module)
    # then
    assert loaded == "[]"


@pytest.mark.parametrize(
    "method",
    ["connect", "ensure_connection", "_cursor", "cursor", "chunked_cursor", "create_cursor"],
)
def test_attempt_counter_sees_a_swallowed_error(alias, method):
    # when
    attempts = swallowed_attempts(alias, method)
    # then
    assert attempts == 1


@EXCEPTIONS
def test_scope_restores_autocommit_when_the_body_raises(alias, exception):
    # given
    wrapper = make_wrapper(alias)
    # when
    caught, between, after = autocommit_after_failing_scope(alias, wrapper, exception)
    # then
    assert (caught, between, after) == (exception.__name__, False, True)


def test_autocommit_defaults_to_true_after_a_scope_exits(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    autocommit_in_scope(alias, wrapper, False)
    # then
    assert AUTOCOMMIT.get() is True


def test_body_that_removed_the_entry_still_propagates_its_own_error(alias):
    # when
    escaped = removed_entry_then_raise(alias, make_wrapper(alias))
    # then
    assert escaped == "ValueError"


def test_apply_server_info_rejects_other_backends_info(alias):
    # given
    wrapper = make_distinct_wrapper(alias)
    # when / then
    with pytest.raises(TypeError, match="PostgreSQLServerInfo"):
        wrapper.apply_server_info(ServerInfo())


def test_scope_rejects_a_wrapper_of_another_alias(alias):
    # given
    wrapper = make_wrapper(alias)
    # when / then
    with (
        pytest.raises(ValueError, match="alias"),
        compile_scope(alias + "_x", wrapper, autocommit=True),
    ):
        pass
