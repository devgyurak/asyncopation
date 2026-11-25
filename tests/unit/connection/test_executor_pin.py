"""Nesting and aliases of `pin()` (ADR-0001 Decision 4)."""

import pytest

from asyncopation import ConcurrentQueryError
from asyncopation.connection.executor import PinnedExecutor, pin
from tests.support.executors.scenarios import (
    ALIAS,
    borrows_in_nested_pins,
    borrows_in_pin,
    borrows_without_pin,
    depth_trace,
    nest_pins,
    other_alias_in_child_of_pin,
    pin_other_alias_in_child,
    pin_two_aliases,
    repin_in_gather_child,
)


async def test_pin_yields_a_pinned_executor_for_the_alias(execenv):
    # when
    async with pin(ALIAS) as ex:
        facts = (isinstance(ex, PinnedExecutor), ex.pinned, ex.alias)
    # then
    assert facts == (True, True, ALIAS)


async def test_a_fresh_pin_is_open_healthy_and_at_depth_one(execenv):
    # when
    async with pin(ALIAS) as ex:
        facts = (ex.closed, ex.broken, ex.depth)
    # then
    assert facts == (False, False, 1)


async def test_leaving_the_outermost_pin_closes_the_executor(execenv):
    # when
    async with pin(ALIAS) as ex:
        pass
    # then
    assert ex.closed is True


async def test_pin_depth_follows_the_nesting(execenv):
    # when
    trace = await depth_trace()
    # then
    assert trace == [1, 2, 1]


async def test_the_same_task_pinning_the_alias_again_reuses_the_executor(execenv):
    # when
    outcome = await nest_pins()
    # then
    assert outcome.same_executor is True


async def test_leaving_an_inner_pin_keeps_the_executor_open_and_usable(execenv):
    # when
    outcome = await nest_pins()
    # then
    assert (outcome.closed_after_inner_exit, outcome.rows_after_inner_exit) == (False, [(1,)])


async def test_leaving_the_outer_pin_closes_a_reused_executor(execenv):
    # when
    outcome = await nest_pins()
    # then
    assert outcome.closed_after_outer_exit is True


async def test_nested_pins_share_one_borrowed_connection(execenv):
    # when
    borrows = await borrows_in_nested_pins(execenv)
    # then
    assert borrows == 1


async def test_a_pin_borrows_one_connection_for_all_its_statements(execenv):
    # when
    borrows = await borrows_in_pin(execenv, 3)
    # then
    assert borrows == 1


async def test_the_connection_is_released_when_the_pin_ends(execenv):
    # given
    await borrows_in_pin(execenv, 2)
    # when
    released = execenv.default.released
    # then
    assert released == [True]


async def test_without_a_pin_every_statement_borrows_its_own_connection(execenv):
    # when
    borrows = await borrows_without_pin(execenv, 3)
    # then
    assert borrows == 3


async def test_pinning_the_same_alias_in_another_task_raises(execenv):
    # when / then
    with pytest.raises(ConcurrentQueryError):
        await repin_in_gather_child()


async def test_refused_repin_borrows_nothing_more(execenv):
    # given
    with pytest.raises(ConcurrentQueryError):
        await repin_in_gather_child()
    # when
    borrows = execenv.default.borrows
    # then
    assert borrows == 1


async def test_pins_on_two_aliases_are_independent_executors(execenv):
    # when
    outcome = await pin_two_aliases(execenv)
    # then
    assert (outcome.distinct, outcome.depths) == (True, (1, 1))


async def test_statements_of_each_pin_reach_their_own_alias(execenv):
    # when
    outcome = await pin_two_aliases(execenv)
    # then
    assert (outcome.default_kinds, outcome.other_kinds) == (["fetch_all"], ["execute"])


async def test_another_alias_is_borrowed_in_a_child_of_a_pin(execenv):
    # when
    pinned, rows, kinds = await other_alias_in_child_of_pin(execenv)
    # then
    assert (pinned, rows, kinds) == (False, [(1,)], ["fetch_all"])


async def test_a_child_of_a_pin_may_pin_a_different_alias(execenv):
    # when
    depth = await pin_other_alias_in_child()
    # then
    assert depth == 1
