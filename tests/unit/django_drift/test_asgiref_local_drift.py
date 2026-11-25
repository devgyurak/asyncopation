"""Pins of ``asgiref.local.Local(thread_critical=True)`` itself (ADR-0008, ledger asgiref row).

The handler-based pins live in ``test_connection_handler_drift.py`` (they go through Django's
``ConnectionHandler``); these use ``Local`` directly so that an asgiref change is told apart from
a Django one.
"""

import pytest

from tests.support.asgiref_local import (
    ABSENT,
    OUTER,
    VALUE,
    awaited_view,
    child_delete_parent_view,
    child_overwrite_views,
    child_set_views,
    delete_missing_error,
    earlier_task_view,
    inherited_context_thread_view,
    later_task_view,
    plain_thread_view,
    sibling_view_of_child_value,
    sync_other_thread_view,
    sync_same_thread_view,
    sync_to_async_view,
    to_thread_view,
)


def test_local_without_a_loop_is_visible_in_the_same_thread():
    # when
    seen = sync_same_thread_view()
    # then
    assert seen == VALUE


def test_local_without_a_loop_is_invisible_to_another_thread():
    # when
    seen = sync_other_thread_view()
    # then
    assert seen is ABSENT


async def test_local_in_a_loop_is_visible_to_awaited_code():
    # when
    seen = await awaited_view()
    # then
    assert seen == VALUE


async def test_local_in_a_loop_is_visible_to_a_task_created_afterwards():
    # when
    seen = await later_task_view()
    # then
    assert seen == VALUE


async def test_local_in_a_loop_is_invisible_to_a_task_created_beforehand():
    # when
    seen = await earlier_task_view()
    # then
    assert seen is ABSENT


async def test_value_set_in_a_child_task_is_invisible_to_the_parent():
    # when
    parent_view, _ = await child_set_views()
    # then
    assert parent_view is ABSENT


async def test_value_set_in_a_child_task_is_visible_to_the_child_itself():
    # when
    _, child_view = await child_set_views()
    # then
    assert child_view == VALUE


async def test_value_set_in_a_task_is_invisible_to_a_sibling_task():
    # when
    seen = await sibling_view_of_child_value()
    # then
    assert seen is ABSENT


async def test_local_in_a_loop_is_invisible_to_to_thread():
    # when
    seen = await to_thread_view()
    # then
    assert seen is ABSENT


@pytest.mark.parametrize("thread_sensitive", [True, False], ids=["sensitive", "insensitive"])
async def test_local_in_a_loop_is_invisible_to_sync_to_async(thread_sensitive):
    # when
    seen = await sync_to_async_view(thread_sensitive=thread_sensitive)
    # then
    assert seen is ABSENT


@pytest.mark.timeout(10)
async def test_local_in_a_loop_is_invisible_to_a_plain_thread():
    # when
    seen = await plain_thread_view()
    # then
    assert seen is ABSENT


@pytest.mark.parametrize("thread_sensitive", [True, False], ids=["sensitive", "insensitive"])
async def test_non_critical_local_is_visible_to_sync_to_async_unlike_the_critical_one(
    thread_sensitive,
):
    # when
    seen = await sync_to_async_view(thread_sensitive=thread_sensitive, thread_critical=False)
    # then
    assert seen == VALUE


@pytest.mark.timeout(10)
async def test_local_in_a_loop_is_invisible_to_a_thread_that_inherited_the_context():
    # when
    seen = await inherited_context_thread_view()
    # then
    assert seen is ABSENT


async def test_overwrite_in_a_child_task_leaves_the_parents_value_alone():
    # when
    parent_view, _ = await child_overwrite_views()
    # then
    assert parent_view == OUTER


async def test_overwrite_in_a_child_task_is_invisible_to_a_sibling_task():
    # when
    _, sibling_view = await child_overwrite_views()
    # then
    assert sibling_view == OUTER


async def test_delete_in_a_child_task_leaves_the_parents_value_alone():
    # when
    parent_view = await child_delete_parent_view()
    # then
    assert parent_view == OUTER


def test_deleting_a_value_that_was_never_set_raises_attribute_error():
    # when
    error = delete_missing_error()
    # then
    assert error == "AttributeError"
