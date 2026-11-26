"""`AsyncObjects` (ADR-0006 Decision 3, ADR-0009 Decision 1): a plain descriptor whose value is a
manager-level object bound to the model and its default manager."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models import FETCH_RAISE, Manager  # ty: ignore[missing-module-member]

from asyncopation import AsyncObjects
from asyncopation.query import AsyncQuerySet
from tests.support.aq_wrapper import has_attribute, read_attribute
from tests.support.manager_models import (
    await_evaluator,
    call_named,
    declared_object,
    receive_in_class_body,
    receive_with_add_to_class,
)
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


def test_aobjects_is_an_async_objects_bound_to_the_model():
    # when
    bound = Plain.aobjects
    # then
    assert isinstance(bound, AsyncObjects)
    assert bound.model is Plain


def test_aobjects_is_not_an_async_queryset():
    # when
    bound = Plain.aobjects
    # then
    assert not isinstance(bound, AsyncQuerySet)


def test_aobjects_is_not_a_django_manager():
    # when
    bound = Plain.aobjects
    # then
    assert not isinstance(bound, Manager)


def test_aobjects_gives_a_new_bound_object_on_every_access():
    # when
    first, second = Plain.aobjects, Plain.aobjects
    # then
    assert first is not second


def test_aobjects_all_gives_a_new_queryset_on_every_call():
    # given
    bound = Plain.aobjects
    # when
    first, second = bound.all(), bound.all()
    # then
    assert first.queryset is not second.queryset


def test_aobjects_all_carries_fetch_raise_so_attribute_access_never_does_io():
    # when
    wrapper = Plain.aobjects.all()
    # then
    assert fetch_mode_of(wrapper) is FETCH_RAISE


def test_aobjects_access_and_all_run_no_query(no_sql):
    # when
    wrapper = Plain.aobjects.all()
    # then
    assert wrapper.queryset._result_cache is None


def test_aobjects_name_is_the_declared_attribute_name():
    # when
    name = Plain.aobjects.name
    # then
    assert name == "aobjects"


def test_a_renamed_descriptor_reports_its_own_name():
    # when
    name = Renamed.async_objects.name
    # then
    assert name == "async_objects"


# -- the default manager is the source -----------------------------------------------------------


def test_aobjects_all_applies_the_filter_of_a_custom_default_manager():
    # when
    conditions = where_conditions(Widget.aobjects.all())
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


def test_aobjects_all_follows_the_all_override_of_the_default_manager():
    # when
    conditions = where_conditions(Overriding.aobjects.all())
    # then
    assert conditions == [("active", "exact")]


def test_aobjects_filter_skips_the_all_override_of_the_default_manager():
    # when
    conditions = where_conditions(Overriding.aobjects.filter())
    # then
    assert conditions == []


def test_aobjects_uses_the_manager_named_by_default_manager_name():
    # when
    conditions = where_conditions(Special.aobjects.all())
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


# -- the unbound declared object ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["alist", "aget", "afirst", "acount", "aexists"])
async def test_an_evaluator_of_the_unbound_declared_object_raises_attribute_error(name):
    # given
    declared = declared_object(Plain)
    # when / then
    with pytest.raises(AttributeError, match=r"not bound to a model"):
        await await_evaluator(declared, name)


@pytest.mark.parametrize("name", ["all", "filter", "get_queryset", "db_manager"])
def test_a_proxied_name_of_the_unbound_declared_object_raises_attribute_error(name):
    # given
    declared = declared_object(Plain)
    # when / then
    with pytest.raises(AttributeError, match=r"not bound to a model"):
        call_named(declared, name)


# -- contribute_to_class is never proxied ---------------------------------------------------------


def test_aobjects_has_no_contribute_to_class():
    # when
    found = has_attribute(Plain.aobjects, "contribute_to_class")
    # then
    assert found is False


def test_the_unbound_declared_object_has_no_contribute_to_class():
    # when
    found = has_attribute(declared_object(Plain), "contribute_to_class")
    # then
    assert found is False


def test_aobjects_in_another_model_body_leaves_the_source_default_manager_untouched():
    # when
    received = receive_in_class_body(Plain)
    # then
    assert received.source_manager_kept is True
    assert received.source_manager_model is Plain


def test_aobjects_in_another_model_body_keeps_its_implicit_objects():
    # when
    received = receive_in_class_body(Plain)
    # then
    assert received.receiver_managers == ["objects"]
    assert received.receiver_objects_model is received.receiver


def test_aobjects_added_to_another_model_leaves_the_source_default_manager_untouched():
    # when
    received = receive_with_add_to_class(Plain)
    # then
    assert received.source_manager_kept is True
    assert received.source_manager_model is Plain


def test_aobjects_added_to_another_model_leaves_its_managers_as_they_were():
    # when
    received = receive_with_add_to_class(Plain)
    # then
    assert received.receiver_managers == ["objects"]
    assert received.receiver_objects_model is received.receiver


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
    bound = attached_content_type.aobjects
    # then
    assert isinstance(bound, AsyncObjects)
    assert bound.model is ContentType


def test_attach_leaves_the_model_managers_alone(attached_content_type):
    # when
    names = manager_names(attached_content_type)
    # then
    assert names == ["objects"]


def test_attach_applies_fetch_raise(attached_content_type):
    # when
    mode = fetch_mode_of(attached_content_type.aobjects.all())
    # then
    assert mode is FETCH_RAISE


def test_attach_with_a_name_reports_it_as_the_name(attached_plain_name):
    # when
    name = read_attribute(Plain, "async_rows").name
    # then
    assert name == "async_rows"


def test_attach_with_a_name_uses_it_in_its_errors(attached_plain_name):
    # given
    instance = Plain()
    # when / then
    with pytest.raises(AttributeError, match=r"async_rows isn't accessible"):
        read_attribute(instance, "async_rows")


def test_attach_over_an_existing_attribute_replaces_it(attached_over_marker):
    # when
    bound = Occupied.marker
    # then
    assert isinstance(bound, AsyncObjects)
    assert bound.model is Occupied


# -- a swapped-out model ---------------------------------------------------------------------------


def test_objects_of_a_swapped_out_model_raises_as_django_does(swapped_model):
    # when / then
    with pytest.raises(AttributeError, match="has been swapped"):
        read_objects(swapped_model)


def test_aobjects_of_a_swapped_out_model_still_returns_a_bound_async_objects(swapped_model, no_sql):
    # when
    bound = swapped_model.aobjects
    # then
    assert isinstance(bound, AsyncObjects)
    assert not isinstance(bound, AsyncQuerySet)
    assert bound.model is swapped_model
