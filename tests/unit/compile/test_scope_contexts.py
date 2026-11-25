"""Exiting a scope from a different contextvars Context (ADR-0008, Decision 1)."""

import pytest

from asyncopation.compile.scope import ScopeContextError
from tests.support.compile_scope import generator_style_close, nested_autocommit, nested_peeks
from tests.support.compile_wrappers import make_distinct_wrapper, make_wrapper
from tests.support.scope_contexts import (
    exit_in_another_context,
    foreign_exit_in_loop,
    foreign_generator_close_in_loop,
)

P = pytest.param
ERRORS = pytest.mark.parametrize(
    "error", [P(None, id="normal-exit"), P(KeyError("boom"), id="exit-with-body-error")]
)
PREVIOUS = pytest.mark.parametrize("previous", [P(True, id="was-true"), P(False, id="was-false")])


@ERRORS
@PREVIOUS
def test_a_foreign_exit_in_a_plain_thread_restores_the_previous_autocommit(alias, error, previous):
    # when
    result = exit_in_another_context(alias, error, previous)
    # then
    assert result.autocommit_after is previous


@ERRORS
def test_a_foreign_exit_in_a_plain_thread_restores_the_entry_and_raises_nothing(alias, error):
    # when
    result = exit_in_another_context(alias, error, previous=True)
    # then
    assert (result.raised, result.peek_after) == (None, None)


def test_a_foreign_exit_with_an_error_in_a_plain_thread_does_not_suppress_it(alias):
    # when
    result = exit_in_another_context(alias, KeyError("boom"), previous=True)
    # then
    assert result.suppressed is False


async def test_foreign_exit_in_a_running_loop_raises_scope_context_error(alias):
    # when
    result = await foreign_exit_in_loop(alias)
    # then
    assert result.error_name == ScopeContextError.__name__


@pytest.mark.parametrize(
    "error", [P(None, id="normal-exit"), P(ValueError("boom"), id="exit-with-body-error")]
)
async def test_foreign_exit_in_a_running_loop_leaves_the_open_scope_untouched(alias, error):
    # when
    result = await foreign_exit_in_loop(alias, error)
    # then
    assert (result.peek_after_attempt, result.autocommit_after_attempt) == (
        result.own_wrapper,
        False,
    )


async def test_the_open_scope_still_restores_everything_when_it_exits(alias):
    # when
    result = await foreign_exit_in_loop(alias)
    # then
    assert (result.peek_after_own_exit, result.autocommit_after_own_exit) == (None, True)


async def test_the_bodys_error_wins_over_scope_context_error(alias):
    # given
    error = ValueError("boom")
    # when
    result = await foreign_exit_in_loop(alias, error)
    # then
    assert (result.error_name, result.suppressed) == ("none", False)


async def test_same_context_nesting_in_a_running_loop_still_restores_stack_wise(alias):
    # given
    outer, inner = make_distinct_wrapper(alias), make_wrapper(alias)
    # when
    seen = nested_peeks(alias, outer, inner)
    # then
    assert seen == [outer, inner, outer, None]


async def test_same_context_nested_autocommit_in_a_running_loop_restores(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    seen = nested_autocommit(alias, wrapper)
    # then
    assert seen == [False, True, False, True]


def test_scope_context_error_is_a_runtime_error():
    # when
    is_runtime_error = issubclass(ScopeContextError, RuntimeError)
    # then
    assert is_runtime_error


def test_scope_module_exports_exactly_its_public_names():
    # given
    from asyncopation.compile import scope

    # when
    exported = scope.__all__
    # then
    assert exported == ["ScopeContextError", "compile_scope"]


async def test_closing_the_generator_of_a_foreign_scope_raises_scope_context_error(alias):
    # when
    result = await foreign_generator_close_in_loop(alias)
    # then
    assert result.error_name == ScopeContextError.__name__


async def test_closing_the_generator_of_a_foreign_scope_leaves_the_open_scope_untouched(alias):
    # when
    result = await foreign_generator_close_in_loop(alias)
    # then
    assert (result.peek_after_attempt, result.autocommit_after_attempt) == (
        result.own_wrapper,
        False,
    )


async def test_closing_the_scope_generator_in_the_same_task_restores_everything(alias):
    # given
    wrapper = make_wrapper(alias)
    # when
    outcome = generator_style_close(alias, wrapper)
    # then
    assert outcome == ("none", False, True)
