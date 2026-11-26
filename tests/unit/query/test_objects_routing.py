"""How `Model.aobjects` resolves a name (ADR-0009 Decisions 3 to 5 and 7): Django's QuerySet copies
go to `aq(manager)`, names the manager defines come from the manager, and every QuerySet comes back
as an `AsyncQuerySet` in `FETCH_RAISE`."""

import pytest
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member]
from django.db.models.query import RawQuerySet

from asyncopation import AsyncObjects, aq
from asyncopation.query import AsyncQuerySet
from tests.support.manager_models import (
    AliasItem,
    ChainedItem,
    DecoratedItem,
    FilterItem,
    Item,
    ModelAsyncObjects,
    Root,
    fetch_mode_of,
    queryset_db,
    where_conditions,
)
from tests.support.object_models import Plain, Widget

# -- Django's copies of QuerySet methods ---------------------------------------------------------


def test_a_copied_queryset_method_returns_an_async_queryset():
    # when
    chained = Plain.aobjects.filter(name="x")
    # then
    assert isinstance(chained, AsyncQuerySet)


def test_a_copied_queryset_method_returns_a_fetch_raise_queryset():
    # when
    chained = Plain.aobjects.filter(name="x")
    # then
    assert fetch_mode_of(chained) is FETCH_RAISE


def test_a_copied_queryset_method_keeps_the_filter_of_the_default_manager():
    # when
    conditions = where_conditions(Widget.aobjects.filter(price__lt=5))
    # then
    assert conditions == [("active", "exact"), ("price", "lt")]


def test_a_user_queryset_method_copied_by_from_queryset_returns_a_fetch_raise_async_queryset():
    # when
    chained = Item.aobjects.cheap()
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert fetch_mode_of(chained) is FETCH_RAISE


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        pytest.param("cheap", [("price", "lt")], id="first-queryset"),
        pytest.param("expensive", [("price", "gte")], id="second-queryset"),
    ],
)
def test_a_chained_from_queryset_exposes_the_methods_of_both_querysets(method, expected):
    # when
    chained = getattr(ChainedItem.aobjects, method)()
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == expected


# -- names the manager defines -------------------------------------------------------------------


def test_a_manager_only_chaining_method_returns_an_async_queryset_with_its_filter():
    # when
    chained = Item.aobjects.published()
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == [("active", "exact")]


def test_a_manager_only_chaining_method_returns_a_fetch_raise_queryset():
    # when
    chained = Item.aobjects.published()
    # then
    assert fetch_mode_of(chained) is FETCH_RAISE


def test_a_manager_only_chaining_method_chains_further():
    # when
    chained = Item.aobjects.published().cheap()
    # then
    assert where_conditions(chained) == [("active", "exact"), ("price", "lt")]


def test_an_alias_of_a_copy_on_the_manager_works_through_the_manager():
    # when
    chained = AliasItem.aobjects.published(active=True)
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == [("active", "exact")]


def test_a_filter_override_on_the_manager_adds_its_condition():
    # when
    chained = FilterItem.aobjects.filter(price__lt=10)
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == [("price", "lt"), ("active", "exact")]


def test_a_decorated_copy_on_the_manager_adds_its_condition():
    # when
    chained = DecoratedItem.aobjects.filter(price__lt=10)
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == [("price", "lt"), ("active", "exact")]


def test_get_queryset_returns_a_fetch_raise_async_queryset():
    # when
    queryset = Plain.aobjects.get_queryset()
    # then
    assert isinstance(queryset, AsyncQuerySet)
    assert fetch_mode_of(queryset) is FETCH_RAISE


def test_all_returns_a_fetch_raise_async_queryset():
    # when
    queryset = Plain.aobjects.all()
    # then
    assert isinstance(queryset, AsyncQuerySet)
    assert fetch_mode_of(queryset) is FETCH_RAISE


def test_a_manager_method_returning_a_raw_queryset_returns_it_unchanged(no_sql):
    # when
    result = Item.aobjects.raw_all()
    # then
    assert isinstance(result, RawQuerySet)


def test_a_manager_method_returning_a_plain_value_returns_it_unchanged():
    # when
    result = Item.aobjects.label()
    # then
    assert result == "manager label"


def test_a_non_callable_manager_attribute_is_returned_as_it_is():
    # when
    alias = Plain.aobjects.db
    # then
    assert alias == "default"


# -- db_manager() --------------------------------------------------------------------------------


def test_db_manager_returns_a_bound_async_objects_of_the_same_class():
    # when
    rebound = Plain.aobjects.db_manager("other")
    # then
    assert type(rebound) is AsyncObjects
    assert rebound.model is Plain


def test_db_manager_keeps_the_subclass():
    # when
    rebound = Root.aobjects.db_manager("other")
    # then
    assert type(rebound) is ModelAsyncObjects


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda rebound: rebound.all(), id="all"),
        pytest.param(lambda rebound: rebound.filter(name="x"), id="filter"),
    ],
)
def test_querysets_of_a_db_manager_use_its_alias(build):
    # given
    rebound = Plain.aobjects.db_manager("other")
    # when
    alias = queryset_db(build(rebound))
    # then
    assert alias == "other"


# -- aq() ----------------------------------------------------------------------------------------


def test_aq_of_aobjects_is_a_fetch_raise_async_queryset_of_the_model():
    # when
    wrapper = aq(Plain.aobjects)
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert fetch_mode_of(wrapper) is FETCH_RAISE
    assert wrapper.model is Plain


def test_aq_of_aobjects_uses_the_default_alias():
    # when
    alias = queryset_db(aq(Plain.aobjects))
    # then
    assert alias == "default"


def test_aq_of_one_bound_object_gives_a_fresh_wrapper_each_call():
    # given
    bound = Plain.aobjects
    # when
    first, second = aq(bound), aq(bound)
    # then
    assert first is not second
    assert first.queryset is not second.queryset


def test_aq_of_aobjects_keeps_the_filter_of_the_default_manager():
    # when
    conditions = where_conditions(aq(Widget.aobjects))
    # then
    assert conditions == [("active", "exact")]


def test_aq_of_a_db_manager_uses_its_alias():
    # when
    wrapper = aq(Plain.aobjects.db_manager("other"))
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert fetch_mode_of(wrapper) is FETCH_RAISE
    assert queryset_db(wrapper) == "other"
