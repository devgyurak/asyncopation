"""The pinned-executor mapping of the context survives blocks that end elsewhere or out of order."""

from asyncopation.connection.executor import BorrowedExecutor
from tests.support.executors.context import (
    current_inside_a_pin_of_another_alias_pinned_first,
    exit_a_pin_from_a_task_that_pins_the_alias_itself,
    exit_two_pins_out_of_order,
    finalize_a_pinning_generator_in_another_task,
    pin_another_alias_inside_a_pin,
    pin_in_a_task_created_inside_a_pin,
    query_in_a_task_created_after_the_block,
)


async def test_a_pin_closed_by_a_finalizer_in_another_task_is_closed(execenv):
    # when
    outcome = await finalize_a_pinning_generator_in_another_task()
    # then
    assert outcome.pin.closed is True


async def test_the_owner_gets_a_borrowed_executor_after_a_finalizer_closed_its_pin(execenv):
    # when
    outcome = await finalize_a_pinning_generator_in_another_task()
    # then
    assert isinstance(outcome.current, BorrowedExecutor)


async def test_the_owner_queries_normally_after_a_finalizer_closed_its_pin(execenv):
    # when
    outcome = await finalize_a_pinning_generator_in_another_task()
    # then
    assert outcome.rows == [(1,)]
    assert outcome.in_flight == 0


async def test_leaving_the_first_of_two_pins_keeps_the_second_one_current(execenv):
    # when
    outcome = await exit_two_pins_out_of_order()
    # then
    assert outcome.current_second_after_first_exit is outcome.second


async def test_leaving_the_first_pin_does_not_close_the_second(execenv):
    # when
    outcome = await exit_two_pins_out_of_order()
    # then
    assert outcome.second_closed_at_that_lookup is False


async def test_the_alias_of_the_pin_that_was_left_first_is_borrowed_again(execenv):
    # when
    outcome = await exit_two_pins_out_of_order()
    # then
    assert isinstance(outcome.current_first_after_first_exit, BorrowedExecutor)


async def test_no_alias_stays_pinned_once_both_pins_were_left(execenv):
    # when
    outcome = await exit_two_pins_out_of_order()
    # then
    assert isinstance(outcome.current_first_after_both_exits, BorrowedExecutor)
    assert isinstance(outcome.current_second_after_both_exits, BorrowedExecutor)


async def test_a_pin_on_another_alias_keeps_the_first_alias_pinned(execenv):
    # when
    outcome = await pin_another_alias_inside_a_pin(execenv)
    # then
    assert outcome.current is outcome.first


async def test_pinning_the_first_alias_again_next_to_another_pin_borrows_nothing_more(execenv):
    # when
    outcome = await pin_another_alias_inside_a_pin(execenv)
    # then
    assert outcome.nested is outcome.first
    assert outcome.borrows == 1


async def test_a_task_created_in_a_pin_can_pin_the_alias_after_the_block_exited(execenv):
    # when
    rows = await pin_in_a_task_created_inside_a_pin()
    # then
    assert rows == [(1,)]


async def test_the_current_executor_inside_a_pin_is_the_pin_whatever_other_aliases_do(execenv):
    # when
    pinned, current = await current_inside_a_pin_of_another_alias_pinned_first()
    # then
    assert current is pinned


async def test_ending_a_pin_from_another_task_keeps_that_tasks_own_pin_current(execenv):
    # when
    outcome = await exit_a_pin_from_a_task_that_pins_the_alias_itself()
    # then
    assert outcome.current_after is outcome.other_pin


async def test_a_task_created_after_the_block_ended_borrows_instead_of_failing(execenv):
    # when
    rows = await query_in_a_task_created_after_the_block()
    # then
    assert rows == [(1,)]
