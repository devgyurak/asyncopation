"""Chaining methods stay lazy and every QuerySet they return is wrapped again."""

import pytest
from django.db.models import FETCH_RAISE, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import (
    COMBINATIONS,
    PREFETCHED_COMBINATIONS,
    combination_operands,
    filled_by_alist_wrapper,
    foreign_mode_identity_wrapper,
    fresh_books,
    identity_queryset,
    prefetched_operands,
    shared_with,
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
    # when
    combined = build(wrapped_empty, wrapped_other, empty, other)
    # then
    operands = (empty, other, wrapped_empty.queryset, wrapped_other.queryset)
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
async def test_a_combination_of_a_prefetched_receiver_makes_no_statement(no_sql, build):
    # given
    _manager, prefetched, wrapped_empty, empty = prefetched_operands()
    # when
    rows = await build(prefetched, wrapped_empty, empty).alist()
    # then
    assert rows == []


def test_a_proxied_method_that_returns_a_prefetched_receiver_shares_its_cache():
    # given
    manager, prefetched, _, _ = prefetched_operands()
    # when
    returned = prefetched.all().all()
    # then
    assert returned.queryset is manager.shared


def test_a_combination_of_a_wrapper_that_alist_filled_is_an_unevaluated_clone():
    # given
    wrapper = filled_by_alist_wrapper()
    _, _, wrapped_empty, _ = prefetched_operands()
    # when
    combined = wrapper | wrapped_empty
    # then
    assert combined.queryset is not wrapper.queryset
    assert combined.queryset._result_cache is None


def test_a_wrapper_passed_as_a_keyword_argument_reaches_django_as_its_queryset():
    # given
    argument = aq(identity_queryset())
    wrapper = aq(identity_queryset())
    # when
    returned = wrapper.echo_keyword(first=argument, other=argument)
    # then
    assert returned.queryset is not argument.queryset
    assert isinstance(returned.queryset, QuerySet)
