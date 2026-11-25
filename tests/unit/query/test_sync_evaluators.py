"""Synchronous evaluators raise SyncEvaluationError before any I/O (ADR-0006)."""

import pytest
from django.db.models import Count

import asyncopation
from asyncopation import aq
from asyncopation.errors import SyncEvaluationError
from tests.support.aq_wrapper import call_named, fresh_books, sync_evaluator_names

EVALUATORS = [
    pytest.param(lambda w: list(w), id="iter"),
    pytest.param(lambda w: len(w), id="len"),
    pytest.param(lambda w: bool(w), id="bool"),
    pytest.param(lambda w: w[0], id="getitem-int"),
    pytest.param(lambda w: w.get(pk=1), id="get"),
    pytest.param(lambda w: w.first(), id="first"),
    pytest.param(lambda w: w.count(), id="count"),
    pytest.param(lambda w: w.exists(), id="exists"),
    pytest.param(lambda w: w.iterator(), id="iterator"),
    pytest.param(lambda w: w.aggregate(n=Count("pk")), id="aggregate"),
    pytest.param(lambda w: w.update(title="x"), id="update"),
    pytest.param(lambda w: w.delete(), id="delete"),
    pytest.param(lambda w: w.create(title="x"), id="create"),
    pytest.param(lambda w: w.get_or_create(title="x"), id="get_or_create"),
    pytest.param(lambda w: w.in_bulk([1]), id="in_bulk"),
]


@pytest.mark.parametrize("evaluate", EVALUATORS)
def test_sync_evaluator_raises_sync_evaluation_error(no_sql, evaluate):
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(SyncEvaluationError):
        evaluate(wrapper)


@pytest.mark.parametrize("name", sync_evaluator_names())
def test_every_listed_sync_evaluator_raises_sync_evaluation_error(no_sql, name):
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(SyncEvaluationError):
        call_named(wrapper, name)


def test_sync_evaluation_error_is_a_runtime_error():
    # when
    bases = SyncEvaluationError.__mro__
    # then
    assert RuntimeError in bases


def test_sync_evaluation_error_is_exported_from_the_package_root():
    # when
    exported = asyncopation.SyncEvaluationError
    # then
    assert exported is SyncEvaluationError


def test_sync_evaluation_error_is_listed_in_the_public_names():
    # when
    public = asyncopation.__all__
    # then
    assert "SyncEvaluationError" in public


def test_slicing_with_a_slice_does_not_raise(no_sql):
    # given
    wrapper = aq(fresh_books())
    # when
    sliced = wrapper[1:3]
    # then
    assert sliced.queryset.query.low_mark == 1
    assert sliced.queryset.query.high_mark == 3
