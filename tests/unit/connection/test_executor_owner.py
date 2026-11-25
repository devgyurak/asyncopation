"""The owner rule of a pinned executor (ADR-0001 Decisions 2, 3, 5)."""

import re

import pytest

from asyncopation import ConcurrentQueryError
from asyncopation.connection.executor import pin
from tests.support.executors.ops import (
    CONTROLS,
    OWNER_MESSAGE,
    STATEMENTS,
    call,
    in_child_task,
    in_gather_child,
    in_taskgroup_child,
    kinds_of,
)
from tests.support.executors.scenarios import (
    ALIAS,
    call_current,
    kinds_after_owner_call,
    owner_use_after_block_exit,
    reenter_during_statement,
    reenter_during_stream,
    savepoint_in_taskgroup_child,
    three_error_messages,
    use_after_block_exit,
)

ALL_OPERATIONS = [*STATEMENTS, *CONTROLS]
OWNER_ERROR = re.escape(OWNER_MESSAGE)


@pytest.mark.parametrize("op", ALL_OPERATIONS)
async def test_owner_task_runs_every_operation_on_its_pinned_executor(execenv, op):
    # when
    kinds = await kinds_after_owner_call(execenv, op)
    # then
    assert kinds == [op]


@pytest.mark.parametrize("op", ALL_OPERATIONS)
async def test_gather_child_using_the_pinned_executor_raises_the_owner_message(execenv, op):
    # when / then
    async with pin(ALIAS) as ex:
        with pytest.raises(ConcurrentQueryError, match=OWNER_ERROR):
            await in_gather_child(call(ex, op))


@pytest.mark.parametrize("op", ALL_OPERATIONS)
async def test_taskgroup_child_using_the_pinned_executor_raises_the_owner_message(execenv, op):
    # when / then
    async with pin(ALIAS) as ex:
        with pytest.raises(ConcurrentQueryError, match=OWNER_ERROR):
            await in_taskgroup_child(call(ex, op))


@pytest.mark.parametrize("op", STATEMENTS)
async def test_child_resolving_the_executor_from_the_context_raises_the_owner_message(execenv, op):
    # when / then
    async with pin(ALIAS):
        with pytest.raises(ConcurrentQueryError, match=OWNER_ERROR):
            await in_gather_child(call_current(ALIAS, op))


async def test_savepoint_from_a_taskgroup_child_raises(execenv):
    # when / then
    with pytest.raises(ConcurrentQueryError, match=OWNER_ERROR):
        await savepoint_in_taskgroup_child()


async def test_refused_child_never_reaches_the_driver(execenv):
    # given
    async with pin(ALIAS) as ex:
        with pytest.raises(ConcurrentQueryError):
            await in_gather_child(call(ex, "fetch_all"))
        # when
        calls = (execenv.default.statement_calls, kinds_of(execenv.default))
    # then
    assert calls == (0, [])


async def test_refused_child_does_not_break_the_owners_transaction(execenv):
    # given
    async with pin(ALIAS) as ex:
        await ex.begin()
        with pytest.raises(ConcurrentQueryError):
            await in_gather_child(call(ex, "fetch_all"))
        # when
        rows = await ex.fetch_all("SELECT 1", None)
        broken = ex.broken
    # then
    assert (rows, broken) == ([(1,)], False)


async def test_owner_message_names_the_remedy(execenv):
    # given
    async with pin(ALIAS) as ex:
        # when
        with pytest.raises(ConcurrentQueryError) as caught:
            await in_gather_child(call(ex, "fetch_all"))
    # then
    assert str(caught.value) == OWNER_MESSAGE


async def test_reentrant_use_while_a_statement_is_in_flight_raises(execenv):
    # when
    outcome = await reenter_during_statement(execenv)
    # then
    assert isinstance(outcome.error, ConcurrentQueryError)


async def test_executor_is_usable_again_after_a_reentrant_use_was_refused(execenv):
    # when
    outcome = await reenter_during_statement(execenv)
    # then
    assert outcome.next_error is None


async def test_reentrant_use_while_a_stream_is_open_raises(execenv):
    # when
    outcome = await reenter_during_stream(execenv)
    # then
    assert isinstance(outcome.error, ConcurrentQueryError)


async def test_executor_is_usable_again_once_the_stream_closed(execenv):
    # when
    outcome = await reenter_during_stream(execenv)
    # then
    assert outcome.next_error is None


async def test_create_task_child_using_the_executor_after_the_block_raises(execenv):
    # when / then
    with pytest.raises(ConcurrentQueryError):
        await use_after_block_exit()


@pytest.mark.parametrize("op", ALL_OPERATIONS)
async def test_owner_using_its_executor_after_the_block_raises(execenv, op):
    # when / then
    with pytest.raises(ConcurrentQueryError):
        await owner_use_after_block_exit(op)


async def test_use_after_the_block_never_reaches_the_driver(execenv):
    # given
    async with pin(ALIAS) as ex:
        pass
    with pytest.raises(ConcurrentQueryError):
        await in_child_task(call(ex, "fetch_all"))
    # when
    calls = execenv.default.statement_calls
    # then
    assert calls == 0


async def test_the_three_refusals_have_three_different_messages(execenv):
    # when
    messages = await three_error_messages(execenv)
    # then
    assert len(set(messages)) == 3
    assert messages[0] == OWNER_MESSAGE
