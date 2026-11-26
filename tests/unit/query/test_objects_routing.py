"""How `Model.aobjects` resolves a name (ADR-0009 Decisions 3 to 5 and 7): Django's QuerySet copies
go to `aq(manager)`, names the manager defines come from the manager, and every QuerySet comes back
as an `AsyncQuerySet` in `FETCH_RAISE`."""

import pytest
from django.db import models
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member]
from django.db.models.query import RawQuerySet

from asyncopation import AsyncObjects, aq
from asyncopation._django.managers import is_queryset_copy
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import has_attribute
from tests.support.manager_models import (
    AliasItem,
    AliasManager,
    BaseManagedItem,
    ChainedItem,
    ChainedManager,
    CountingItem,
    DecoratedItem,
    DecoratedManager,
    FilterItem,
    FilterOverrideManager,
    Item,
    ItemManager,
    ModelAsyncObjects,
    OwnedItem,
    Root,
    StoredItem,
    aq_manager_hook,
    call_twice,
    chains_during,
    clear_stored,
    get_queryset_calls_during,
    manager_with_instance_attribute,
    queryset_db,
    queryset_fetch_mode,
    set_instance_all,
    store_querysets,
    unset_instance_all,
)
from tests.support.object_models import Plain, Widget, fetch_mode_of, where_conditions


@pytest.fixture
def stored():
    """A fresh unevaluated (`shared`) and a filled-cache (`filled`) QuerySet that `StoredItem`'s
    manager methods hand out, held by the test."""
    yield store_querysets()
    clear_stored()


@pytest.fixture
def instance_all(stored):
    """`all` set on the INSTANCE of `StoredItem`'s default manager, returning `stored["shared"]`."""
    manager = set_instance_all(StoredItem, stored["shared"])
    yield stored
    unset_instance_all(manager)


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


# -- a copy is reached lazily, with one clone ----------------------------------------------------


def test_reading_a_copied_method_runs_no_get_queryset():
    # when
    calls = get_queryset_calls_during(lambda: CountingItem.aobjects.filter)
    # then
    assert calls == 0


def test_hasattr_of_a_copied_method_runs_no_get_queryset():
    # when
    calls = get_queryset_calls_during(lambda: has_attribute(CountingItem.aobjects, "filter"))
    # then
    assert calls == 0


def test_each_call_of_a_copied_method_runs_get_queryset_once():
    # given
    method = CountingItem.aobjects.filter
    # when
    calls = get_queryset_calls_during(lambda: call_twice(method))
    # then
    assert calls == 2


def test_a_copied_method_builds_one_queryset_clone():
    # when
    chains = chains_during(lambda: Plain.aobjects.filter(name="x"))
    # then
    assert chains == 1


@pytest.mark.parametrize(
    ("build", "name", "expected"),
    [
        pytest.param(models.Manager, "filter", True, id="manager-filter"),
        pytest.param(ItemManager, "cheap", True, id="from-queryset-user-method"),
        pytest.param(ChainedManager, "cheap", True, id="chained-first-queryset"),
        pytest.param(ChainedManager, "expensive", True, id="chained-second-queryset"),
        pytest.param(AliasManager, "published", False, id="alias-of-a-copy"),
        pytest.param(FilterOverrideManager, "filter", False, id="filter-override"),
        pytest.param(DecoratedManager, "filter", False, id="decorated-copy"),
        pytest.param(models.Manager, "all", False, id="manager-all"),
        pytest.param(
            lambda: manager_with_instance_attribute("filter"),
            "filter",
            False,
            id="instance-attribute",
        ),
    ],
)
def test_is_queryset_copy_tells_django_copies_from_manager_names(build, name, expected):
    # given
    manager = build()
    # when
    copied = is_queryset_copy(manager, name)
    # then
    assert copied is expected


# -- QuerySets a manager method hands out that the test holds ------------------------------------


def test_a_shared_queryset_from_a_manager_method_is_cloned(stored):
    # when
    wrapper = StoredItem.aobjects.shared()
    # then
    assert wrapper.queryset is not stored["shared"]
    assert fetch_mode_of(wrapper) is FETCH_RAISE


def test_a_shared_queryset_from_a_manager_method_keeps_its_fetch_mode(stored):
    # given
    before = queryset_fetch_mode(stored["shared"])
    # when
    StoredItem.aobjects.shared()
    # then
    assert queryset_fetch_mode(stored["shared"]) is before


def test_a_filled_queryset_from_a_manager_method_is_wrapped_as_it_is(no_sql, stored):
    # when
    wrapper = StoredItem.aobjects.filled()
    # then
    assert wrapper.queryset is stored["filled"]


def test_an_all_set_on_the_manager_instance_gives_a_clone(instance_all):
    # when
    wrapper = StoredItem.aobjects.all()
    # then
    assert wrapper.queryset is not instance_all["shared"]


def test_an_all_set_on_the_manager_instance_leaves_the_fetch_mode_alone(instance_all):
    # given
    before = queryset_fetch_mode(instance_all["shared"])
    # when
    StoredItem.aobjects.all()
    # then
    assert queryset_fetch_mode(instance_all["shared"]) is before


# -- managers handed out -------------------------------------------------------------------------


def test_db_manager_of_a_base_manager_default_is_rebound():
    # when
    rebound = BaseManagedItem.aobjects.db_manager("other")
    # then
    assert type(rebound) is ModelAsyncObjects
    assert rebound.model is BaseManagedItem
    assert queryset_db(rebound.all()) == "other"


def test_a_manager_of_another_model_is_rebound_as_a_plain_async_objects():
    # when
    rebound = OwnedItem.aobjects.other_manager()
    # then
    assert type(rebound) is AsyncObjects
    assert rebound.model is Plain


# -- the aq() hook ---------------------------------------------------------------------------------


def test_the_aq_manager_hook_is_the_name_mangled_method():
    # when
    hook = aq_manager_hook()
    # then
    assert hook == "_AsyncObjects__aq_manager"
    assert has_attribute(AsyncObjects, hook) is True
