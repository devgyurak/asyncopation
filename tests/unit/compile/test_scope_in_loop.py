"""The scope restores correctly inside a running loop (asgiref storage is contextvar-backed)."""

import asyncio

import pytest

from tests.support import connection_entry as connections
from tests.support.compile_scope import failing_scope, nested_peeks, peek_inside_scope
from tests.support.compile_wrappers import make_distinct_wrapper, make_wrapper


async def test_in_loop_scope_restores_the_previous_wrapper(alias):
    # given
    previous, scoped = make_distinct_wrapper(alias), make_wrapper(alias)
    connections.install(alias, previous)
    # when
    inside = peek_inside_scope(alias, scoped)
    # then
    assert (inside, connections.peek(alias)) == (scoped, previous)


async def test_in_loop_scope_removes_the_entry_when_there_was_none(alias):
    # given
    scoped = make_wrapper(alias)
    # when
    inside = peek_inside_scope(alias, scoped)
    # then
    assert (inside, connections.peek(alias)) == (scoped, None)


@pytest.mark.parametrize("exception", [ValueError, asyncio.CancelledError, KeyboardInterrupt])
async def test_in_loop_scope_restores_the_previous_wrapper_when_the_body_raises(alias, exception):
    # given
    previous, scoped = make_distinct_wrapper(alias), make_wrapper(alias)
    connections.install(alias, previous)
    # when
    caught, after = failing_scope(alias, scoped, exception)
    # then
    assert (caught, after) == (exception.__name__, previous)


@pytest.mark.parametrize("exception", [ValueError, asyncio.CancelledError, KeyboardInterrupt])
async def test_in_loop_scope_removes_the_entry_when_the_body_raises(alias, exception):
    # when
    caught, after = failing_scope(alias, make_wrapper(alias), exception)
    # then
    assert (caught, after) == (exception.__name__, None)


async def test_in_loop_nested_scopes_restore_stack_wise(alias):
    # given
    outer, inner = make_distinct_wrapper(alias), make_wrapper(alias)
    # when
    seen = nested_peeks(alias, outer, inner)
    # then
    assert seen == [outer, inner, outer, None]
