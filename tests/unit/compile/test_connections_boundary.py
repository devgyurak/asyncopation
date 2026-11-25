"""`asyncopation._django.connections.swapped` and the scope's handling of foreign Contexts."""

import asyncio

import pytest

from asyncopation._django import connections as boundary_module
from tests.support import connection_entry as entry
from tests.support.compile_scope import (
    body_error_through_scope,
    swapped_entry_deleted_inside,
    swapped_failing,
    swapped_nested,
    swapped_peeks,
)
from tests.support.compile_wrappers import make_distinct_wrapper, make_wrapper

EXCEPTIONS = pytest.mark.parametrize(
    "exception", [ValueError, asyncio.CancelledError, KeyboardInterrupt]
)


def test_swapped_restores_the_previous_entry(alias):
    # given
    previous, scoped = make_distinct_wrapper(alias), make_wrapper(alias)
    entry.install(alias, previous)
    # when
    inside, after = swapped_peeks(alias, scoped)
    # then
    assert (inside, after) == (scoped, previous)


def test_swapped_removes_the_entry_when_there_was_none(alias):
    # given
    scoped = make_wrapper(alias)
    # when
    inside, after = swapped_peeks(alias, scoped)
    # then
    assert (inside, after, entry.has_entry(alias)) == (scoped, None, False)


@EXCEPTIONS
def test_swapped_restores_the_previous_entry_when_the_body_raises(alias, exception):
    # given
    previous = make_distinct_wrapper(alias)
    entry.install(alias, previous)
    # when
    caught, after = swapped_failing(alias, make_wrapper(alias), exception)
    # then
    assert (caught, after) == (exception.__name__, previous)


@EXCEPTIONS
def test_swapped_removes_the_entry_when_the_body_raises_and_there_was_none(alias, exception):
    # when
    caught, after = swapped_failing(alias, make_wrapper(alias), exception)
    # then
    assert (caught, after, entry.has_entry(alias)) == (exception.__name__, None, False)


def test_swapped_nests_stack_wise(alias):
    # given
    outer, inner = make_distinct_wrapper(alias), make_wrapper(alias)
    # when
    seen = swapped_nested(alias, outer, inner)
    # then
    assert seen == [outer, inner, outer, None]


def test_swapped_tolerates_an_entry_deleted_inside_the_body(alias):
    # when
    escaped = swapped_entry_deleted_inside(alias, make_wrapper(alias), raises=False)
    # then
    assert escaped == "none"


def test_swapped_does_not_replace_the_bodys_error_when_the_entry_was_deleted(alias):
    # when
    escaped = swapped_entry_deleted_inside(alias, make_wrapper(alias), raises=True)
    # then
    assert escaped == "ValueError"


@pytest.mark.parametrize("name", ["connections", "peek", "install", "remove"])
def test_the_boundary_module_hides_djangos_handler_and_raw_operations(name):
    # when
    exposed = hasattr(boundary_module, name)
    # then
    assert exposed is False


def test_the_boundary_module_exports_only_its_public_names():
    # when
    exported = boundary_module.__all__
    # then
    assert exported == ["ScopeContextError", "configured_databases", "swapped"]


def test_the_bodys_exception_is_not_replaced_by_the_restore(alias):
    # when
    escaped = body_error_through_scope(alias, make_wrapper(alias))
    # then
    assert escaped == "KeyError"
