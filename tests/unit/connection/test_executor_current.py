"""`current_executor(alias)`: the pinned executor of the context, else a borrowed one."""

from asyncopation.connection.executor import BorrowedExecutor, PinnedExecutor, current_executor, pin
from tests.support.executors.scenarios import (
    ALIAS,
    current_after_block,
    current_in_child_after_exit,
    current_in_gather_child,
)


async def test_without_a_pin_the_current_executor_is_borrowed(execenv):
    # when
    executor = current_executor(ALIAS)
    # then
    assert isinstance(executor, BorrowedExecutor)
    assert (executor.pinned, executor.alias) == (False, ALIAS)


async def test_inside_a_pin_the_current_executor_is_the_pinned_one(execenv):
    # when
    async with pin(ALIAS) as ex:
        current = current_executor(ALIAS)
    # then
    assert current is ex


async def test_a_pin_on_another_alias_does_not_change_the_current_executor(execenv):
    # when
    async with pin("other"):
        current = current_executor(ALIAS)
    # then
    assert isinstance(current, BorrowedExecutor)


async def test_a_gather_child_sees_the_pinned_executor_of_its_parent(execenv):
    # when
    pinned, seen = await current_in_gather_child()
    # then
    assert seen is pinned


async def test_after_the_block_the_owner_gets_a_borrowed_executor_again(execenv):
    # when
    executor = await current_after_block()
    # then
    assert isinstance(executor, BorrowedExecutor)


async def test_a_child_that_copied_the_context_gets_the_closed_pin_after_the_block(execenv):
    # when
    executor = await current_in_child_after_exit()
    # then
    assert isinstance(executor, PinnedExecutor)
    assert executor.closed is True
