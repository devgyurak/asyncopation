"""Which refusal wins when several apply: the owner rule comes before busy and broken."""

import pytest

from asyncopation import ConcurrentQueryError
from tests.support.executors.ops import (
    CONTROLS,
    CONTROLS_THAT_RAISE_WHEN_BROKEN,
    OWNER_MESSAGE,
    STATEMENTS,
)
from tests.support.executors.ownership import (
    child_rollback_with_an_open_stream,
    child_use_during_an_owner_stream,
    child_use_of_a_broken_pin,
    child_use_of_a_broken_pin_logged,
)


async def test_a_child_rollback_is_refused_before_it_closes_the_owners_stream(execenv):
    # when
    outcome = await child_rollback_with_an_open_stream(execenv)
    # then
    assert isinstance(outcome.error, ConcurrentQueryError)
    assert outcome.owner_read_error is None


async def test_a_refused_child_rollback_sends_nothing_to_the_driver(execenv):
    # when
    outcome = await child_rollback_with_an_open_stream(execenv)
    # then
    assert outcome.kinds == ["begin", "stream"]


async def test_a_child_rollback_on_a_broken_pin_raises_concurrent_query_error(execenv):
    # when
    error = await child_use_of_a_broken_pin(execenv, "rollback")
    # then
    assert type(error) is ConcurrentQueryError
    assert str(error) == OWNER_MESSAGE


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS_THAT_RAISE_WHEN_BROKEN])
async def test_a_child_using_a_broken_pin_raises_concurrent_query_error(execenv, op):
    # when
    error = await child_use_of_a_broken_pin(execenv, op)
    # then
    assert type(error) is ConcurrentQueryError
    assert str(error) == OWNER_MESSAGE


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_child_using_the_pin_while_the_owners_stream_is_open_gets_the_owner_message(
    execenv, op
):
    # when
    error = await child_use_during_an_owner_stream(execenv, op)
    # then
    assert type(error) is ConcurrentQueryError
    assert str(error) == OWNER_MESSAGE


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS_THAT_RAISE_WHEN_BROKEN])
async def test_a_child_refused_on_a_broken_pin_never_reaches_the_driver(execenv, op):
    # when
    kinds = await child_use_of_a_broken_pin_logged(execenv, op)
    # then
    assert kinds == ["begin", "fetch_all"]
