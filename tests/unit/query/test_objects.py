"""`AsyncObjects` (ADR-0006 Decision 3): a plain descriptor over the model's default manager."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member]

from asyncopation import AsyncObjects
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import read_attribute
from tests.support.object_models import (
    Base,
    Child,
    Concrete,
    Occupied,
    Overriding,
    Parent,
    Plain,
    ProxyPlain,
    Renamed,
    Special,
    Widget,
    attach_over_marker,
    attach_to_content_type,
    default_manager_name,
    detach_from_content_type,
    fetch_mode_of,
    manager_names,
    restore_marker,
    where_conditions,
)
from tests.support.swapped_model import read_objects, swapped_out_model


@pytest.fixture
def attached_content_type():
    """`AsyncObjects.attach(ContentType)`, undone afterwards (the model is shared by the session)."""
    model = attach_to_content_type()
    yield model
    detach_from_content_type()


@pytest.fixture
def attached_plain_name():
    """`AsyncObjects.attach(Plain, "async_rows")`, undone afterwards."""
    AsyncObjects.attach(Plain, "async_rows")
    yield "async_rows"
    delattr(Plain, "async_rows")


@pytest.fixture
def swapped_model():
    """A model with `objects` and `aobjects` whose swappable setting names another model."""
    with swapped_out_model() as model:
        yield model


@pytest.fixture
def attached_over_marker():
    """`AsyncObjects.attach(Occupied, "marker")` over a plain class attribute, restored afterwards."""
    attach_over_marker()
    yield
    restore_marker()


# -- what Model.aobjects is ----------------------------------------------------------------------


def test_aobjects_is_an_async_queryset_of_the_model():
    # when
    wrapper = Plain.aobjects
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert wrapper.model is Plain


def test_aobjects_gives_a_fresh_queryset_on_every_access():
    # when
    first, second = Plain.aobjects, Plain.aobjects
    # then
    assert first is not second
    assert first.queryset is not second.queryset


def test_aobjects_carries_fetch_raise_so_attribute_access_never_does_io():
    # when
    wrapper = Plain.aobjects
    # then
    assert fetch_mode_of(wrapper) is FETCH_RAISE


def test_aobjects_has_not_run_a_query(no_sql):
    # when
    wrapper = Plain.aobjects
    # then
    assert wrapper.queryset._result_cache is None


# -- the default manager is the source -----------------------------------------------------------


def test_aobjects_applies_the_filter_of_a_custom_default_manager():
    # when
    conditions = where_conditions(Widget.aobjects)
    # then
    assert conditions == [("active", "exact")]


def test_aobjects_chains_the_custom_queryset_methods_of_the_default_manager():
    # when
    chained = Widget.aobjects.cheap()
    # then
    assert isinstance(chained, AsyncQuerySet)


def test_a_chained_custom_queryset_method_keeps_the_filter_of_the_default_manager():
    # when
    conditions = where_conditions(Widget.aobjects.cheap())
    # then
    assert conditions == [("active", "exact"), ("price", "lt")]


def test_aobjects_adds_no_filter_of_a_default_manager_that_overrides_all():
    # when
    conditions = where_conditions(Overriding.aobjects)
    # then
    assert conditions == []


def test_aobjects_uses_the_manager_named_by_default_manager_name():
    # when
    conditions = where_conditions(Special.aobjects)
    # then
    assert default_manager_name(Special) == "special"
    assert conditions == [("active", "exact")]


# -- it is not a manager -------------------------------------------------------------------------


def test_declaring_only_aobjects_keeps_objects_as_the_default_manager():
    # when
    name = default_manager_name(Plain)
    # then
    assert name == "objects"


def test_declaring_aobjects_adds_nothing_to_the_model_managers():
    # when
    names = manager_names(Plain)
    # then
    assert names == ["objects"]


def test_aobjects_does_not_replace_a_custom_default_manager():
    # when
    names, default = manager_names(Widget), default_manager_name(Widget)
    # then
    assert names == ["objects"]
    assert default == "objects"


def test_aobjects_is_a_descriptor_not_a_manager():
    # when
    declared = vars(Plain)["aobjects"]
    # then
    assert isinstance(declared, AsyncObjects)


# -- where it refuses ----------------------------------------------------------------------------


def test_aobjects_on_an_instance_raises_attribute_error():
    # given
    instance = Plain()
    # when / then
    with pytest.raises(AttributeError, match=r"aobjects isn't accessible via Plain instances"):
        read_attribute(instance, "aobjects")


def test_aobjects_read_without_an_owner_raises_attribute_error():
    # given
    descriptor = AsyncObjects()
    # when / then
    with pytest.raises(AttributeError, match=r"aobjects needs the model class"):
        descriptor.__get__(None)


def test_the_error_names_the_attribute_the_descriptor_was_declared_under():
    # given
    instance = Renamed()
    # when / then
    with pytest.raises(AttributeError, match=r"async_objects isn't accessible"):
        read_attribute(instance, "async_objects")


def test_a_renamed_descriptor_works_on_the_class():
    # when
    wrapper = Renamed.async_objects
    # then
    assert wrapper.model is Renamed


def test_aobjects_on_an_abstract_model_raises_attribute_error():
    # when / then
    with pytest.raises(AttributeError, match=r"abstract"):
        read_attribute(Base, "aobjects")


def test_aobjects_is_inherited_by_a_concrete_child_of_an_abstract_model():
    # when
    wrapper = Concrete.aobjects
    # then
    assert wrapper.model is Concrete


# -- inheritance ---------------------------------------------------------------------------------


def test_aobjects_of_a_multi_table_child_uses_the_child_model():
    # when
    wrapper = Child.aobjects
    # then
    assert wrapper.model is Child


def test_aobjects_of_the_parent_stays_the_parent_model():
    # when
    wrapper = Parent.aobjects
    # then
    assert wrapper.model is Parent


def test_aobjects_of_a_proxy_model_uses_the_proxy_model():
    # when
    wrapper = ProxyPlain.aobjects
    # then
    assert wrapper.model is ProxyPlain


# -- attach --------------------------------------------------------------------------------------


def test_attach_adds_aobjects_to_a_model_that_cannot_declare_it(attached_content_type):
    # when
    wrapper = attached_content_type.aobjects
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert wrapper.model is ContentType


def test_attach_leaves_the_model_managers_alone(attached_content_type):
    # when
    names = manager_names(attached_content_type)
    # then
    assert names == ["objects"]


def test_attach_applies_fetch_raise(attached_content_type):
    # when
    mode = fetch_mode_of(attached_content_type.aobjects)
    # then
    assert mode is FETCH_RAISE


def test_attach_with_a_name_uses_it_in_its_errors(attached_plain_name):
    # given
    instance = Plain()
    # when / then
    with pytest.raises(AttributeError, match=r"async_rows isn't accessible"):
        read_attribute(instance, "async_rows")


def test_attach_over_an_existing_attribute_replaces_it(attached_over_marker):
    # when
    wrapper = Occupied.marker
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert wrapper.model is Occupied


# -- a swapped-out model ---------------------------------------------------------------------------


def test_objects_of_a_swapped_out_model_raises_as_django_does(swapped_model):
    # when / then
    with pytest.raises(AttributeError, match="has been swapped"):
        read_objects(swapped_model)


def test_aobjects_of_a_swapped_out_model_still_returns_an_async_queryset(swapped_model, no_sql):
    # when
    wrapper = swapped_model.aobjects
    # then
    assert isinstance(wrapper, AsyncQuerySet)
    assert wrapper.model is swapped_model
