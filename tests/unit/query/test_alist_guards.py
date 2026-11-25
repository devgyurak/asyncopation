"""`alist()` paths that need no database: pre-filled caches and unsupported prefetching."""

import pytest

from asyncopation import aq
from tests.support.aq_wrapper import fresh_books, prefilled_queryset


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
