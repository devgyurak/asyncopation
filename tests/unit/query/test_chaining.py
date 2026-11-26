"""Chaining methods stay lazy and every QuerySet they return is wrapped again."""

import pytest
from django.db.models import FETCH_RAISE, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import (
    COMBINATIONS,
    combination_operands,
    fresh_books,
    identity_queryset,
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
