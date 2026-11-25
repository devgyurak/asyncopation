"""How `aq()` and the wrapper behave under Python protocols: copy, pickle, operators, hasattr."""

import copy
import pickle

import pytest
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation import aq
from asyncopation.errors import SyncEvaluationError
from asyncopation.query import AsyncQuerySet
from harness.catalog.models import Book
from tests.support.aq_wrapper import (
    DuckSource,
    filtered_pair,
    fresh_books,
    has_attribute,
    related_books_manager,
    sql_of,
)


def test_aq_of_a_non_queryset_raises_type_error():
    # when / then
    with pytest.raises(TypeError, match="QuerySet or a Manager"):
        aq(42)


def test_aq_of_a_related_manager_wraps_the_filtered_queryset():
    # when
    wrapper = aq(related_books_manager())
    # then
    assert wrapper.queryset.model is Book
    assert "author_id" in sql_of(wrapper)


def test_aq_of_a_related_manager_keeps_the_known_related_object():
    # when
    wrapper = aq(related_books_manager())
    # then
    assert len(wrapper.queryset._known_related_objects) == 1


def test_aq_of_an_object_with_get_queryset_wraps_that_queryset():
    # when
    wrapper = aq(DuckSource())
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert wrapper.queryset.model is Book


@pytest.mark.parametrize("duplicate", [copy.copy, copy.deepcopy])
def test_copying_a_wrapper_returns_another_wrapper(no_sql, duplicate):
    # given
    wrapper = aq(fresh_books())
    # when
    duplicated = duplicate(wrapper)
    # then
    assert isinstance(duplicated, AsyncQuerySet)
    assert duplicated is not wrapper


@pytest.mark.parametrize("duplicate", [copy.copy, copy.deepcopy])
def test_copying_a_wrapper_keeps_the_raise_fetch_mode(no_sql, duplicate):
    # given
    wrapper = aq(fresh_books())
    # when
    duplicated = duplicate(wrapper)
    # then
    assert duplicated.queryset._fetch_mode is FETCH_RAISE


def test_pickling_a_wrapper_raises_sync_evaluation_error_and_runs_no_query(no_sql):
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(SyncEvaluationError):
        pickle.dumps(wrapper)


@pytest.mark.parametrize(
    ("combine", "plain"),
    [
        pytest.param(lambda a, b: aq(a) | aq(b), lambda a, b: a | b, id="or-both-wrapped"),
        pytest.param(lambda a, b: aq(a) & aq(b), lambda a, b: a & b, id="and-both-wrapped"),
        pytest.param(lambda a, b: aq(a) | b, lambda a, b: a | b, id="or-right-plain"),
        pytest.param(lambda a, b: aq(a) & b, lambda a, b: a & b, id="and-right-plain"),
    ],
)
def test_combined_wrapper_sql_equals_the_sql_of_the_plain_operator(no_sql, combine, plain):
    # given
    left, right = filtered_pair()
    # when
    combined = combine(left, right)
    # then
    assert isinstance(combined, AsyncQuerySet)
    assert sql_of(combined) == sql_of(plain(left, right))


def test_hasattr_of_a_sync_evaluator_name_raises_sync_evaluation_error():
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(SyncEvaluationError):
        has_attribute(wrapper, "count")
