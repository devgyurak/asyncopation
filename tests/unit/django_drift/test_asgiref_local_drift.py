"""Pins of ``asgiref.local.Local(thread_critical=True)`` itself (ADR-0008, ledger asgiref row).

The handler-based pins live in ``test_connection_handler_drift.py`` (they go through Django's
``ConnectionHandler``); these use ``Local`` directly so that an asgiref change is told apart from
a Django one.
"""

import pytest

from tests.support.asgiref_local import (
    ABSENT,
    VALUE,
    awaited_view,
    child_set_views,
    earlier_task_view,
    is_thread_critical_local,
    later_task_view,
    make_local,
    plain_thread_view,
    sibling_view_of_child_value,
    sync_other_thread_view,
    sync_same_thread_view,
    sync_to_async_view,
    to_thread_view,
)


def test_thread_critical_flag_selects_the_thread_local_backed_storage():
    # when
    critical = is_thread_critical_local(make_local(thread_critical=True))
    # then
    assert critical


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
    parent_view, child_view = await child_set_views()
    # then
    assert (parent_view, child_view) == (ABSENT, VALUE)


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


async def test_local_in_a_loop_is_invisible_to_a_plain_thread():
    # when
    seen = await plain_thread_view()
    # then
    assert seen is ABSENT


async def test_non_critical_local_is_visible_to_sync_to_async_unlike_the_critical_one():
    # when
    seen = await sync_to_async_view(thread_sensitive=True, thread_critical=False)
    # then
    assert seen == VALUE
