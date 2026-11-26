"""Code that evaluates synchronously behind `Model.aobjects` (ADR-0009 Decision 8): a user QuerySet
method that evaluates internally and a sync manager method that queries raise Django's
`SynchronousOnlyOperation` inside a running event loop, with no async statement and no thread hop."""

import pytest
from django.core.exceptions import SynchronousOnlyOperation

from tests.support.manager_models import Item, attach_to_member, detach_from_member


@pytest.fixture
def attached_member():
    """`AsyncObjects.attach(Member)` (a model with Django's `BaseUserManager`), undone afterwards."""
    model = attach_to_member()
    yield model
    detach_from_member()


async def test_a_queryset_method_that_evaluates_internally_raises_synchronous_only_operation(
    no_async_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = Item.aobjects
    # when / then
    with pytest.raises(SynchronousOnlyOperation):
        bound.first_name()


async def test_a_sync_manager_method_that_queries_raises_synchronous_only_operation(
    attached_member, no_async_sql, no_thread_hop, no_sync_to_async
):
    # given
    bound = attached_member.aobjects
    # when / then
    with pytest.raises(SynchronousOnlyOperation):
        bound.get_by_natural_key("someone")
