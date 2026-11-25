"""Async methods that are not implemented yet raise NotImplementedError and never fall through
to Django's `sync_to_async` (which would silently hop to a thread)."""

import pytest

from asyncopation import aq
from tests.support.aq_wrapper import call_named, fresh_books, unimplemented_async_names


@pytest.mark.parametrize("name", unimplemented_async_names())
def test_unimplemented_async_method_raises_not_implemented(no_sql, no_thread_hop, name):
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(NotImplementedError):
        call_named(wrapper, name)


def test_acreate_raises_not_implemented_without_a_thread_hop(no_sql, no_thread_hop):
    # given
    wrapper = aq(fresh_books())
    # when / then
    with pytest.raises(NotImplementedError):
        wrapper.acreate(title="x")


def test_unimplemented_names_cover_the_planned_methods():
    # when
    names = unimplemented_async_names()
    # then
    assert {"acreate", "aget", "afirst", "acount", "aexists", "aaggregate"} <= set(names)
    assert "alist" not in names
