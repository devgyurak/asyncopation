"""Django still copies QuerySet methods onto managers the way ADR-0009 Decision 6 relies on: every
copy shares one `manager_method` code object, and a copy dispatches through `get_queryset()`."""

import pytest
from django.db.models import Manager
from django.db.models.manager import BaseManager

from tests.support.manager_drift import (
    MANAGER_METHOD_QUALNAME,
    code_of,
    dispatch_of_copy,
    generated_base,
    helper_manager_method_code,
)


def test_two_copied_methods_share_one_code_object():
    # when
    filter_code, exclude_code = Manager.filter.__code__, Manager.exclude.__code__
    # then
    assert filter_code is exclude_code


def test_the_shared_code_object_is_the_manager_method_of_get_queryset_methods():
    # when
    qualname = Manager.filter.__code__.co_qualname
    # then
    assert qualname == MANAGER_METHOD_QUALNAME


def test_manager_filter_is_a_copy_in_the_generated_base():
    # given
    base = generated_base()
    # when
    code = code_of(base, "filter")
    # then
    assert "filter" not in vars(Manager)
    assert code is Manager.filter.__code__
    assert vars(base)["filter"].__name__ == "filter"


@pytest.mark.parametrize("name", ["all", "get_queryset", "db_manager"])
def test_a_manager_own_method_is_defined_on_base_manager(name):
    # when
    defined = name in vars(BaseManager)
    # then
    assert defined is True


@pytest.mark.parametrize("name", ["all", "get_queryset", "db_manager"])
def test_a_manager_own_method_is_not_a_copy(name):
    # when
    code = code_of(BaseManager, name)
    # then
    assert code is not Manager.filter.__code__


def test_a_copy_dispatches_through_get_queryset():
    # when
    dispatch = dispatch_of_copy("filter", 1, active=True)
    # then
    assert dispatch.result == "result of filter"
    assert dispatch.calls == [("filter", (1,), {"active": True})]


def test_the_helper_takes_the_manager_method_code_object_from_django():
    # when
    code = helper_manager_method_code()
    # then
    assert code is Manager.filter.__code__
