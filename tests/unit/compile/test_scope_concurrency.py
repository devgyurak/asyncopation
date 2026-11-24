"""compile_scope under concurrent tasks and cancellation (ADR-0008, Decisions 1 and 3).

A scope never yields to the loop (no `await` inside it), so interleaving inside a scope is
impossible by contract; these tests only interleave tasks *between* scopes. Tests about
bystanders and cancellation pin asyncio/contextvar isolation of the swap, not extra logic of
`compile_scope`: they would also pass for any contextvar-backed swap that restores in `finally`.
"""

from tests.support.compile_wrappers import make_wrapper
from tests.support.scope_asyncio import (
    bystander_view,
    cancel_after_scope,
    cancel_before_scope,
    cancelled_inside_scope,
    gather_mixed_autocommit,
)


async def test_gathered_tasks_in_different_transaction_modes_compile_independently(alias):
    # when
    outcomes = await gather_mixed_autocommit(alias)
    # then
    assert outcomes == [("TransactionManagementError", False), (None, True)]


async def test_cancelled_task_that_already_left_its_scope_leaves_nothing_installed(alias):
    # given: pins asyncio isolation; the scope was exited before the cancellation arrived
    wrapper = make_wrapper(alias)
    # when
    in_task, in_parent = await cancel_after_scope(alias, wrapper)
    # then
    assert (in_task, in_parent) == (None, None)


async def test_cancelled_task_that_never_entered_a_scope_leaves_nothing_installed(alias):
    # given: pins asyncio isolation; the task is cancelled while awaiting before its scope
    wrapper = make_wrapper(alias)
    # when
    in_task, in_parent = await cancel_before_scope(alias, wrapper)
    # then
    assert (in_task, in_parent) == (None, None)


async def test_bystander_task_never_sees_another_tasks_scope(alias):
    # given: pins asyncio isolation of the contextvar-backed storage
    # when
    before, after = await bystander_view(alias)
    # then
    assert (before, after) == (None, None)


async def test_cancellation_raised_in_the_scope_body_restores_the_entry(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    in_task, in_parent = await cancelled_inside_scope(alias, wrapper)
    # then
    assert (in_task, in_parent) == (None, None)
