"""`alist()` paths that need no database: pre-filled caches and unsupported prefetching."""

import pytest

from asyncopation import aq
from tests.support.aq_wrapper import (
    alist_after_mutating_the_result,
    fresh_books,
    prefilled_queryset,
)


async def test_alist_of_a_prefilled_cache_returns_it_without_io(no_sql):
    # given
    sentinel = object()
    wrapper = aq(prefilled_queryset([sentinel]))
    # when
    rows = await wrapper.alist()
    # then
    assert rows == [sentinel]
    assert isinstance(rows, list)


async def test_alist_of_a_prefetch_related_queryset_raises_not_implemented(no_sql):
    # given
    wrapper = aq(fresh_books().prefetch_related("tags"))
    # when / then
    with pytest.raises(NotImplementedError):
        await wrapper.alist()


async def test_alist_of_a_prefilled_cache_returns_a_copy_not_the_cache_list(no_sql):
    # given
    wrapper = aq(prefilled_queryset([object()]))
    # when
    rows = await wrapper.alist()
    # then
    assert rows is not wrapper.queryset._result_cache


async def test_changing_the_alist_result_does_not_change_the_cache(no_sql):
    # given
    sentinel = object()
    wrapper = aq(prefilled_queryset([sentinel]))
    # when
    again = await alist_after_mutating_the_result(wrapper)
    # then
    assert again == [sentinel]
