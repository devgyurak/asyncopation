"""Chaining methods stay lazy and every QuerySet they return is wrapped again."""

import pytest
from django.db.models import FETCH_RAISE, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import (
    CACHED_ROW,
    COMBINATIONS,
    PREFETCHED_COMBINATIONS,
    combination_operands,
    filled_by_alist_wrapper,
    foreign_mode_identity_wrapper,
    fresh_books,
    identity_queryset,
    prefetched_identity_manager,
    prefetched_operands,
    shared_with,
    stored_raise_mode_wrapper,
)

CHAINS = [
    pytest.param(lambda w: w.filter(published=True), id="filter"),
    pytest.param(lambda w: w.exclude(published=True), id="exclude"),
    pytest.param(lambda w: w.order_by("-pk"), id="order_by"),
    pytest.param(lambda w: w.values("title"), id="values"),
    pytest.param(lambda w: w.values_list("title", flat=True), id="values_list"),
    pytest.param(lambda w: w.select_related("author"), id="select_related"),
    pytest.param(lambda w: w.only("title"), id="only"),
    pytest.param(lambda w: w.defer("metadata"), id="defer"),
    pytest.param(lambda w: w.distinct(), id="distinct"),
    pytest.param(lambda w: w.dates("published_at", "year"), id="dates"),
    pytest.param(lambda w: w[:3], id="slice"),
    pytest.param(lambda w: w.all(), id="all"),
]


@pytest.mark.parametrize("chain", CHAINS)
def test_chaining_method_returns_an_async_queryset(chain):
    # given
    wrapper = aq(fresh_books())
    # when
    chained = chain(wrapper)
    # then
    assert isinstance(chained, AsyncQuerySet)


@pytest.mark.parametrize("chain", CHAINS)
def test_chaining_method_wraps_a_queryset_in_raise_fetch_mode(chain):
    # given
    wrapper = aq(fresh_books())
    # when
    chained = chain(wrapper)
    # then
    assert isinstance(chained.queryset, QuerySet)
    assert chained.queryset._fetch_mode is FETCH_RAISE


def test_chaining_does_not_execute_sql(no_sql):
    # given
    wrapper = aq(fresh_books())
    # when
    chained = wrapper.filter(published=True).order_by("pk").select_related("author")
    # then
    assert isinstance(chained, AsyncQuerySet)


def test_chaining_does_not_change_the_original_wrapper():
    # given
    wrapper = aq(fresh_books())
    sql_before = str(wrapper.queryset.query)
    # when
    wrapper.filter(published=True)
    # then
    assert str(wrapper.queryset.query) == sql_before


def test_chained_wrapper_sql_matches_django():
    # when
    chained = aq(fresh_books()).filter(published=True).order_by("-pk")
    # then
    assert str(chained.queryset.query) == str(
        fresh_books().filter(published=True).order_by("-pk").query
    )


@pytest.mark.parametrize("build", COMBINATIONS)
def test_a_combination_never_wraps_a_queryset_that_one_of_its_operands_holds(build):
    # given
    empty, other, wrapped_empty, wrapped_other = combination_operands()
    operands = (empty, other, wrapped_empty.queryset, wrapped_other.queryset)
    # when
    combined = build(wrapped_empty, wrapped_other, empty, other)
    # then
    assert shared_with(combined, *operands) == [False, False, False, False]


@pytest.mark.parametrize("build", COMBINATIONS)
def test_a_combination_is_wrapped_in_fetch_raise(build):
    # given
    empty, other, wrapped_empty, wrapped_other = combination_operands()
    # when
    combined = build(wrapped_empty, wrapped_other, empty, other)
    # then
    assert combined.queryset._fetch_mode is FETCH_RAISE


def test_a_user_method_that_returns_its_receiver_is_wrapped_as_a_clone():
    # given
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.same()
    # then
    assert returned.queryset is not wrapper.queryset


def test_a_user_method_that_returns_an_argument_is_wrapped_as_a_clone():
    # given
    argument = identity_queryset()
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo(argument)
    # then
    assert returned.queryset is not argument


def test_a_user_method_that_returns_a_foreign_mode_receiver_is_wrapped_in_fetch_raise():
    # given
    wrapper = foreign_mode_identity_wrapper()
    # when
    returned = wrapper.same()
    # then
    assert returned.queryset is not wrapper.queryset
    assert returned.queryset._fetch_mode is FETCH_RAISE


def test_a_user_method_that_returns_a_foreign_mode_argument_is_wrapped_in_fetch_raise():
    # given
    argument = fresh_books()
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo(argument)
    # then
    assert returned.queryset is not argument
    assert returned.queryset._fetch_mode is FETCH_RAISE


def test_a_user_method_that_returns_a_keyword_argument_is_wrapped_as_a_clone():
    # given
    argument = identity_queryset()
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo_keyword(first=identity_queryset(), other=argument)
    # then
    assert returned.queryset is not argument


def test_a_wrapper_passed_as_an_argument_reaches_django_as_its_queryset():
    # given
    _empty, other, wrapped_empty, wrapped_other = combination_operands()
    # when
    combined = wrapped_other.union(wrapped_empty, wrapped_other)
    # then
    assert isinstance(combined, AsyncQuerySet)
    assert isinstance(combined.queryset, QuerySet)
    assert str(combined.queryset.query) == str(other.union(other).query)


@pytest.mark.parametrize("build", PREFETCHED_COMBINATIONS)
def test_a_combination_that_returns_a_prefetched_receiver_shares_its_cache(build):
    # given
    manager, prefetched, wrapped_empty, empty = prefetched_operands()
    # when
    combined = build(prefetched, wrapped_empty, empty)
    # then
    assert combined.queryset is manager.shared


@pytest.mark.parametrize("build", PREFETCHED_COMBINATIONS)
def test_all_of_a_combination_that_shares_a_prefetched_cache_is_a_new_queryset(build):
    # given
    manager, prefetched, wrapped_empty, empty = prefetched_operands()
    combined = build(prefetched, wrapped_empty, empty)
    # when
    again = combined.all()
    # then
    assert again.queryset is not manager.shared
    assert again.queryset._result_cache is None


@pytest.mark.parametrize("build", PREFETCHED_COMBINATIONS)
async def test_a_combination_of_a_prefetched_receiver_makes_no_statement(no_sql, build):
    # given
    _manager, prefetched, wrapped_empty, empty = prefetched_operands()
    # when
    rows = await build(prefetched, wrapped_empty, empty).alist()
    # then
    assert rows == [CACHED_ROW]


def test_a_combination_of_two_wrappers_one_of_them_prefetched_is_a_new_queryset():
    # given
    manager, prefetched, _, _ = prefetched_operands()
    other = aq(fresh_books())
    # when
    combined = prefetched | other
    # then
    assert combined.queryset is not manager.shared
    assert combined.queryset._result_cache is None


def test_all_of_a_combination_of_two_wrappers_one_of_them_prefetched_is_a_new_queryset():
    # given
    _manager, prefetched, _, _ = prefetched_operands()
    combined = prefetched | aq(fresh_books())
    # when
    again = combined.all()
    # then
    assert again.queryset is not combined.queryset


def test_all_of_a_prefetched_wrapper_shares_its_cache():
    # given
    manager, prefetched, _, _ = prefetched_operands()
    # when
    returned = prefetched.all()
    # then
    assert returned.queryset is manager.shared


def test_all_of_all_of_a_prefetched_wrapper_is_a_new_queryset():
    # given
    manager, prefetched, _, _ = prefetched_operands()
    # when
    returned = prefetched.all().all()
    # then
    assert returned.queryset is not manager.shared
    assert returned.queryset._result_cache is None


def test_a_user_method_that_returns_the_receiver_of_a_prefetched_wrapper_shares_its_cache():
    # given
    manager = prefetched_identity_manager()
    # when
    returned = aq(manager).same()
    # then
    assert returned.queryset is manager.shared


def test_all_of_a_user_method_result_that_shares_a_prefetched_cache_is_a_new_queryset():
    # given
    manager = prefetched_identity_manager()
    returned = aq(manager).same()
    # when
    again = returned.all()
    # then
    assert again.queryset is not manager.shared


def test_a_combination_of_a_wrapper_that_alist_filled_shares_that_cache():
    # given
    wrapper = filled_by_alist_wrapper()
    _, _, wrapped_empty, _ = prefetched_operands()
    # when
    combined = wrapper | wrapped_empty
    # then
    assert combined.queryset is wrapper.queryset


def test_all_of_a_combination_of_a_wrapper_that_alist_filled_is_a_new_queryset():
    # given
    wrapper = filled_by_alist_wrapper()
    _, _, wrapped_empty, _ = prefetched_operands()
    combined = wrapper | wrapped_empty
    # when
    again = combined.all()
    # then
    assert again.queryset is not wrapper.queryset
    assert again.queryset._result_cache is None


@pytest.mark.parametrize("stored", ["shared_raise_mode", "shared_other_mode"])
def test_a_user_method_that_returns_an_unfilled_stored_queryset_is_cloned_into_fetch_raise(stored):
    # given
    wrapper = stored_raise_mode_wrapper()
    shared = getattr(wrapper.queryset, stored)()
    # when
    returned = getattr(wrapper, stored)()
    # then
    assert returned.queryset is not shared
    assert returned.queryset._fetch_mode is FETCH_RAISE


def test_a_user_method_that_returns_a_stored_queryset_with_a_filled_cache_shares_it():
    # given
    wrapper = stored_raise_mode_wrapper()
    shared = wrapper.queryset.shared_cached()
    # when
    returned = wrapper.shared_cached()
    # then
    assert returned.queryset is shared


async def test_a_stored_queryset_with_a_filled_cache_that_a_user_method_returns_makes_no_statement(
    no_sql,
):
    # given
    wrapper = stored_raise_mode_wrapper()
    # when
    rows = await wrapper.shared_cached().alist()
    # then
    assert rows == [CACHED_ROW]


def test_a_user_method_that_returns_its_second_argument_is_wrapped_as_a_clone():
    # given
    second = identity_queryset()
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo_second(identity_queryset(), second)
    # then
    assert returned.queryset is not second


def test_a_wrapper_passed_as_a_keyword_argument_reaches_django_as_its_queryset():
    # given
    argument = aq(identity_queryset())
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo_keyword(first=argument, other=argument)
    # then
    assert returned.queryset is not argument.queryset
    assert isinstance(returned.queryset, QuerySet)
