"""Pins of the asgiref / Django behaviour the scoped swap relies on (ADR-0008)."""

from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.models.sql.compiler import SQLCompiler
from django.utils.connection import BaseConnectionHandler

from tests.support import connection_entry as boundary
from tests.support.connection_drift import (
    injected_version_and_attempts,
    source_contains,
    static_is_cached_property,
)
from tests.support.scope_asyncio import (
    Marker,
    child_install_visibility,
    later_task_sees_parent_swap,
    plain_thread_sees_parent_swap,
    sibling_install_visibility,
    to_thread_sees_parent_swap,
)


async def test_install_in_a_child_task_is_invisible_to_the_parent(alias):
    # when
    parent_view, child_view = await child_install_visibility(alias)
    # then
    assert (parent_view, type(child_view)) == (None, Marker)


async def test_install_in_a_task_is_invisible_to_a_sibling_task(alias):
    # when
    seen = await sibling_install_visibility(alias)
    # then
    assert seen is None


async def test_swap_in_the_parent_is_visible_to_a_task_created_afterwards(alias):
    # when
    marker, seen = await later_task_sees_parent_swap(alias)
    # then
    assert seen is marker


async def test_swap_in_the_loop_thread_is_invisible_to_to_thread(alias):
    # when
    seen = await to_thread_sees_parent_swap(alias)
    # then
    assert seen is None


async def test_swap_in_the_loop_thread_is_invisible_to_a_plain_thread(alias):
    # when
    seen = plain_thread_sees_parent_swap(alias)
    # then
    assert seen is None


def test_peek_does_not_create_a_wrapper(alias):
    # when
    seen = boundary.peek(alias)
    # then
    assert seen is None


def test_pg_version_is_a_cached_property_on_djangos_wrapper():
    # when
    cached = static_is_cached_property(DatabaseWrapper, "pg_version")
    # then
    assert cached


def test_injecting_pg_version_into_the_instance_dict_avoids_connecting(alias):
    # when
    served, attempts = injected_version_and_attempts(alias, 170000)
    # then
    assert (served, attempts) == (170000, 0)


def test_cursor_goes_through_the_private_cursor_method():
    # when
    found = source_contains(BaseDatabaseWrapper.cursor, "self._cursor(")
    # then
    assert found


def test_get_autocommit_ensures_a_connection():
    # when
    found = source_contains(BaseDatabaseWrapper.get_autocommit, "self.ensure_connection()")
    # then
    assert found


def test_select_for_update_compilation_reads_get_autocommit():
    # when
    found = source_contains(SQLCompiler.as_sql, "get_autocommit()")
    # then
    assert found


def test_connection_handler_stores_connections_in_a_local():
    # when
    found = source_contains(BaseConnectionHandler.__init__, "self._connections = Local(")
    # then
    assert found


def test_connection_handler_is_thread_critical():
    # when
    critical = type(connections).thread_critical
    # then
    assert critical is True
