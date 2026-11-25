"""`aq()` wraps a QuerySet by composition: construction, idempotence, cache and fetch mode."""

from django.db.models import FETCH_ONE, FETCH_RAISE, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from harness.catalog.models import Book
from tests.support.aq_wrapper import book_manager, fresh_books, prefilled_queryset


def test_aq_of_a_queryset_returns_an_async_queryset():
    # when
    wrapper = aq(fresh_books())
    # then
    assert isinstance(wrapper, AsyncQuerySet)


def test_aq_of_a_manager_wraps_its_queryset():
    # when
    wrapper = aq(book_manager())
    # then
    assert isinstance(wrapper.queryset, QuerySet)
    assert wrapper.queryset.model is Book


def test_aq_of_an_aq_returns_the_same_wrapper():
    # given
    wrapper = aq(fresh_books())
    # when
    again = aq(wrapper)
    # then
    assert again is wrapper


def test_aq_applies_the_raise_fetch_mode_to_the_wrapped_queryset():
    # when
    wrapper = aq(fresh_books())
    # then
    assert wrapper.queryset._fetch_mode is FETCH_RAISE


def test_aq_does_not_mutate_the_queryset_it_was_given():
    # given
    original = fresh_books()
    # when
    aq(original)
    # then
    assert original._fetch_mode is FETCH_ONE


def test_aq_of_an_evaluated_queryset_wraps_it_without_cloning():
    # given
    evaluated = prefilled_queryset([])
    # when
    wrapper = aq(evaluated)
    # then
    assert wrapper.queryset is evaluated
    assert wrapper.queryset._result_cache == []


def test_aq_of_an_unevaluated_queryset_clones_it():
    # given
    original = fresh_books()
    # when
    wrapper = aq(original)
    # then
    assert wrapper.queryset is not original
