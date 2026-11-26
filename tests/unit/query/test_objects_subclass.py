"""`AsyncObjects` subclasses (ADR-0009 Decisions 2 and 3): async manager methods, overridable
evaluators, the protected `model` / `name`, and the lookup order of a subclass's own names."""

import pytest

from asyncopation import aq
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import call_named, read_attribute
from tests.support.manager_models import (
    AbstractRootChild,
    DbItem,
    DelegatingItem,
    FaultyItem,
    MarkingItem,
    ModelAsyncObjects,
    OwnedItem,
    Root,
    RootChild,
    RootProxy,
    SilentHookItem,
    SlottedItem,
    StatefulItem,
    attach_subclass_to_bare,
    declared_object,
    define_mixin_subclass_with,
    define_subclass_with,
    detach_from_bare,
    model_declaring_subclass_with,
    record_evaluators,
)
from tests.support.object_models import where_conditions
from tests.support.same_name_objects import SameNameItem


@pytest.fixture
def attached_bare():
    """`ModelAsyncObjects.attach(Bare)`, undone afterwards."""
    model = attach_subclass_to_bare()
    yield model
    detach_from_bare()


@pytest.fixture
def evaluator_calls(monkeypatch):
    """The five `AsyncQuerySet` evaluators replaced by recording stubs (no database)."""
    return record_evaluators(monkeypatch)


# -- the bound model inside a subclass method -----------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(Root, id="declaring-model"),
        pytest.param(RootChild, id="multi-table-child"),
        pytest.param(RootProxy, id="proxy-model"),
        pytest.param(AbstractRootChild, id="concrete-child-of-an-abstract-model"),
    ],
)
async def test_a_subclass_method_sees_the_accessed_class_as_self_model(model):
    # when
    seen = await model.aobjects.amodel()
    # then
    assert seen is model


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(Root, id="declaring-model"),
        pytest.param(RootChild, id="multi-table-child"),
        pytest.param(RootProxy, id="proxy-model"),
        pytest.param(AbstractRootChild, id="concrete-child-of-an-abstract-model"),
    ],
)
async def test_an_evaluator_of_a_subclass_queries_the_accessed_class(evaluator_calls, model):
    # when
    await model.aobjects.acount()
    # then
    assert evaluator_calls[0].queryset.model is model


def test_aobjects_of_a_model_declaring_a_subclass_is_an_instance_of_that_subclass():
    # when
    bound = Root.aobjects
    # then
    assert type(bound) is ModelAsyncObjects


def test_attach_of_a_subclass_binds_that_subclass(attached_bare):
    # when
    bound = attached_bare.aobjects
    # then
    assert type(bound) is ModelAsyncObjects
    assert bound.model is attached_bare


# -- model and name cannot be shadowed ------------------------------------------------------------


@pytest.mark.parametrize("attribute", ["model", "name"])
def test_a_subclass_defining_a_protected_name_raises_type_error(attribute):
    # when / then
    with pytest.raises(TypeError, match=r"shadow"):
        define_subclass_with(attribute)


@pytest.mark.parametrize("attribute", ["model", "name"])
def test_a_subclass_whose_mixin_defines_a_protected_name_raises_type_error(attribute):
    # when / then
    with pytest.raises(TypeError, match=r"shadow"):
        define_mixin_subclass_with(attribute)


# -- private names of AsyncObjects are free for a subclass ----------------------------------------


@pytest.mark.parametrize("attribute", ["_model", "_manager", "_bind", "_aq_manager"])
def test_a_subclass_reusing_a_private_name_binds_the_model(attribute):
    # given
    model = model_declaring_subclass_with(attribute)
    # when
    bound = model.aobjects
    # then
    assert bound.model is model


@pytest.mark.parametrize("attribute", ["_model", "_manager", "_bind", "_aq_manager"])
def test_a_subclass_reusing_a_private_name_keeps_aq_working(attribute):
    # given
    model = model_declaring_subclass_with(attribute)
    # when
    wrapper = aq(model.aobjects)
    # then
    assert wrapper.model is model


@pytest.mark.parametrize("attribute", ["_model", "_manager", "_bind", "_aq_manager"])
def test_a_subclass_reusing_a_private_name_keeps_copies_working(attribute):
    # given
    model = model_declaring_subclass_with(attribute)
    # when
    chained = model.aobjects.filter(name="x")
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert chained.model is model


# -- state and properties of a subclass -----------------------------------------------------------


def test_a_bound_object_carries_a_slot_its_subclass_set_in_init():
    # when
    plain = SlottedItem.aobjects.plain
    # then
    assert plain == "tagged"


def test_a_bound_object_carries_a_private_slot_its_subclass_set_in_init():
    # when
    secret = SlottedItem.aobjects.secret()
    # then
    assert secret == "hidden"


def test_a_bound_object_carries_a_slot_below_a_class_that_skips_init_subclass():
    # when
    depth = SilentHookItem.aobjects.depth
    # then
    assert depth == "below"


def test_a_subclass_property_raising_attribute_error_falls_back_to_the_manager():
    # when
    label = call_named(FaultyItem.aobjects, "label")
    # then
    assert label == "manager label"


def test_a_subclass_property_raising_attribute_error_names_it_when_the_manager_lacks_it():
    # given
    bound = FaultyItem.aobjects
    # when / then
    with pytest.raises(
        AttributeError, match=r"FaultyPropertyAsyncObjects\.missing raised AttributeError"
    ):
        read_attribute(bound, "missing")


def test_a_subclass_property_reaches_the_manager_value_through_super_getattr():
    # when
    alias = DbItem.aobjects.db
    # then
    assert alias == "via:default"


# -- a subclass named AsyncObjects with its own private attribute --------------------------------


def test_a_same_named_subclass_with_a_private_attribute_binds_the_model():
    # when
    bound = SameNameItem.aobjects
    # then
    assert bound.model is SameNameItem
    assert bound.helper_value() == "helper"


def test_a_same_named_subclass_with_a_private_attribute_keeps_aq_working():
    # when
    wrapper = aq(SameNameItem.aobjects)
    # then
    assert wrapper.model is SameNameItem


def test_a_same_named_subclass_with_a_private_attribute_keeps_copies_working():
    # when
    chained = SameNameItem.aobjects.filter(name="x")
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert chained.model is SameNameItem


# -- overriding an evaluator ----------------------------------------------------------------------


async def test_an_evaluator_override_runs_and_reaches_the_default_through_super(evaluator_calls):
    # when
    result = await MarkingItem.aobjects.aget(pk=1)
    # then
    assert result == ("override", ("default", "aget", (), {"pk": 1}))


async def test_the_default_reached_through_super_evaluates_the_bound_model(evaluator_calls):
    # when
    await MarkingItem.aobjects.aget(pk=1)
    # then
    assert evaluator_calls[0].queryset.model is MarkingItem


async def test_an_evaluator_override_does_not_affect_a_chain(evaluator_calls):
    # when
    result = await MarkingItem.aobjects.filter(name="x").aget(pk=1)
    # then
    assert result == ("default", "aget", (), {"pk": 1})


# -- the subclass's own names come first ----------------------------------------------------------


def test_a_subclass_method_named_like_a_blocked_evaluator_is_called(no_sql):
    # when
    result = OwnedItem.aobjects.get()
    # then
    assert result == "own get"


def test_a_subclass_method_named_like_a_manager_method_is_called(no_sql):
    # when
    result = OwnedItem.aobjects.published()
    # then
    assert result == "own published"


def test_a_subclass_reaches_the_manager_method_it_overrides_through_super_getattr():
    # when
    chained = DelegatingItem.aobjects.published()
    # then
    assert isinstance(chained, AsyncQuerySet)
    assert where_conditions(chained) == [("active", "exact")]


async def test_super_of_an_evaluator_that_does_not_exist_yet_raises_attribute_error(
    no_sql, no_thread_hop
):
    # given
    bound = DelegatingItem.aobjects
    # when / then
    with pytest.raises(AttributeError, match=r"acreate"):
        await bound.acreate(name="x")


def test_a_bound_object_carries_the_state_its_subclass_set_in_init():
    # given
    declared = declared_object(StatefulItem)
    # when
    bound = StatefulItem.aobjects
    # then
    assert bound.calls is declared.calls
