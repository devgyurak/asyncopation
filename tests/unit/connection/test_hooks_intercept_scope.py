"""Where an interceptor applies and in which order the chain runs (issue #4, ADR-0005 Decision 4):
the same task-tree scope as `observe()`, global interceptors outside scoped ones, the first
registered outermost, and observers around the whole chain."""

from tests.support.hooks.intercepting import (
    child_of_intercepting_parent,
    global_and_scoped_interceptors,
    global_interceptor_removed,
    intercepted_before_and_after_the_block,
    interceptor_of_a_sibling,
    nested_intercept_blocks,
    observer_around_interceptor,
    two_global_interceptors,
    two_scoped_interceptors,
)


async def test_scoped_interceptors_run_with_the_first_registered_outermost(execenv):
    # when
    log = await two_scoped_interceptors()
    # then
    assert log == ["first:before", "second:before", "second:after", "first:after"]


async def test_global_interceptors_run_with_the_first_registered_outermost(execenv):
    # when
    log = await two_global_interceptors()
    # then
    assert log == ["first:before", "second:before", "second:after", "first:after"]


async def test_a_global_interceptor_runs_outside_a_scoped_one_registered_before_it(execenv):
    # when
    log = await global_and_scoped_interceptors()
    # then
    assert log == ["global:before", "scoped:before", "scoped:after", "global:after"]


async def test_nested_intercept_blocks_run_the_outer_one_outermost_until_the_inner_exits(execenv):
    # when
    log = await nested_intercept_blocks()
    # then
    assert log == [
        "outer:before",
        "inner:before",
        "inner:after",
        "outer:after",
        "inner-exited",
        "outer:before",
        "outer:after",
    ]


async def test_observers_wrap_the_whole_interceptor_chain(execenv):
    # when
    log = await observer_around_interceptor()
    # then
    assert log == [
        "observer:query_started",
        "interceptor:before",
        "interceptor:after",
        "observer:query_finished",
    ]


async def test_a_gather_child_inherits_the_interceptor_of_its_parent(execenv):
    # when
    interceptor = await child_of_intercepting_parent()
    # then
    assert interceptor.sqls == ["SELECT 'child'"]


async def test_an_interceptor_of_one_child_does_not_apply_to_its_sibling(execenv):
    # when
    interceptor = await interceptor_of_a_sibling()
    # then
    assert interceptor.sqls == ["SELECT 'a'"]


async def test_an_interceptor_does_not_apply_after_its_block_exited(execenv):
    # when
    interceptor = await intercepted_before_and_after_the_block()
    # then
    assert interceptor.sqls == ["SELECT 'inside'"]


async def test_a_removed_global_interceptor_does_not_apply_any_more(execenv):
    # when
    interceptor = await global_interceptor_removed()
    # then
    assert interceptor.sqls == ["SELECT 'before'"]
