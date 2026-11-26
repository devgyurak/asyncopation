"""`Model.aobjects` keeps a `Manager`'s protocols (ADR-0009 Decision 5): no QuerySet operations,
copy / deepcopy / pickle of the object itself, a manager-level `repr()`, uncached evaluators."""

import copy
import pickle

import pytest
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member]

from asyncopation import AsyncObjects
from asyncopation.query.objects import EVALUATORS
from tests.support.aq_wrapper import has_attribute, read_attribute
from tests.support.manager_models import (
    alist_twice,
    call_names,
    collected,
    distinct_querysets,
    gather_two,
    iterated,
    or_combined,
    record_evaluators,
    shares_manager,
    sliced,
)
from tests.support.object_models import Plain, Widget, fetch_mode_of, where_conditions


@pytest.fixture
def evaluator_calls(monkeypatch):
    """The five `AsyncQuerySet` evaluators replaced by recording stubs (no database)."""
    return record_evaluators(monkeypatch)


# -- QuerySet operations need .all() first -------------------------------------------------------


def test_slicing_aobjects_raises_type_error(no_sql):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(TypeError):
        sliced(bound)


def test_or_of_aobjects_raises_type_error(no_sql):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(TypeError):
        or_combined(bound)


def test_iter_of_aobjects_raises_type_error(no_sql):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(TypeError):
        iterated(bound)


async def test_async_for_over_aobjects_raises_type_error(no_sql):
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(TypeError):
        await collected(bound)


def test_aobjects_defines_no_aiter():
    # when
    found = has_attribute(Plain.aobjects, "__aiter__")
    # then
    assert found is False


def test_the_queryset_attribute_of_aobjects_raises_attribute_error():
    # given
    bound = Plain.aobjects
    # when / then
    with pytest.raises(AttributeError):
        read_attribute(bound, "queryset")


# -- copy, deepcopy and pickle of the object itself ----------------------------------------------


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(lambda bound: pickle.loads(pickle.dumps(bound)), id="pickle"),
    ],
)
def test_a_duplicate_of_aobjects_is_bound_to_the_same_model(no_sql, duplicate):
    # given
    bound = Plain.aobjects
    # when
    result = duplicate(bound)
    # then
    assert type(result) is AsyncObjects
    assert result.model is Plain


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(lambda bound: pickle.loads(pickle.dumps(bound)), id="pickle"),
    ],
)
def test_a_duplicate_of_aobjects_keeps_the_default_manager_filter(no_sql, duplicate):
    # given
    bound = Widget.aobjects
    # when
    result = duplicate(bound)
    # then
    assert where_conditions(result.all()) == [("active", "exact")]


@pytest.mark.parametrize(
    ("duplicate", "shared"),
    [
        pytest.param(copy.copy, True, id="copy"),
        pytest.param(copy.deepcopy, False, id="deepcopy"),
        pytest.param(lambda bound: pickle.loads(pickle.dumps(bound)), False, id="pickle"),
    ],
)
def test_only_a_shallow_copy_shares_the_bound_manager(no_sql, duplicate, shared):
    # given
    bound = Plain.aobjects
    # when
    result = duplicate(bound)
    # then
    assert shares_manager(result, bound) is shared


def test_repr_of_aobjects_runs_no_sql(no_sql):
    # when
    text = repr(Plain.aobjects)
    # then
    assert isinstance(text, str)


# -- evaluators never cache ----------------------------------------------------------------------


@pytest.mark.parametrize("name", EVALUATORS)
async def test_an_evaluator_runs_on_a_fetch_raise_queryset_of_the_bound_model(
    evaluator_calls, name
):
    # given
    bound = Widget.aobjects
    # when
    result = await getattr(bound, name)()
    # then
    assert result == ("default", name, (), {})
    assert evaluator_calls[0].queryset.model is Widget
    assert fetch_mode_of(evaluator_calls[0]) is FETCH_RAISE
    assert where_conditions(evaluator_calls[0]) == [("active", "exact")]


async def test_aget_passes_its_arguments_on(evaluator_calls):
    # when
    result = await Plain.aobjects.aget(pk=1)
    # then
    assert result == ("default", "aget", (), {"pk": 1})


async def test_two_alist_calls_on_one_bound_object_evaluate_twice(evaluator_calls):
    # given
    bound = Plain.aobjects
    # when
    await alist_twice(bound)
    # then
    assert call_names(evaluator_calls) == ["alist", "alist"]
    assert distinct_querysets(evaluator_calls) == 2


async def test_gathered_evaluators_on_one_bound_object_share_no_queryset(evaluator_calls):
    # given
    bound = Plain.aobjects
    # when
    await gather_two(bound)
    # then
    assert sorted(call_names(evaluator_calls)) == ["acount", "alist"]
    assert distinct_querysets(evaluator_calls) == 2
