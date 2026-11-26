"""Errors of the read APIs that Django raises before any database I/O, and Asyncopation too.

No module-wide `XFAIL_UNTIL_ASYNCMY` here: these cases never open a driver, so they pass on every
backend (including MySQL and MariaDB before #7); a strict xfail would turn them red there.
"""

from functools import partial

import pytest
from django.db.models import Count

from harness.catalog.models import AuthorProfile, Book
from tests.support.read_api import compare


def _union():
    return Book.objects.filter(pk__lt=10).union(Book.objects.filter(pk__gt=5))


def _sliced():
    return Book.objects.order_by("pk")[2:7]


def _unordered_aggregate():
    return AuthorProfile.objects.values("website").annotate(n=Count("pk"))


async def test_aget_after_a_combinator_with_filters_raises_the_same_not_supported_error(
    async_env, book_pk
):
    # when
    pair = await compare(_union, "aget", pk=book_pk)
    # then
    assert pair.sync.error_type == "django.db.utils.NotSupportedError"
    assert pair.async_ == pair.sync


async def test_aget_on_a_sliced_queryset_raises_the_same_type_error(async_env, book_pk):
    # when
    pair = await compare(_sliced, "aget", pk=book_pk)
    # then
    assert pair.sync.error_type == "builtins.TypeError"
    assert pair.async_ == pair.sync


async def test_afirst_of_an_unordered_aggregating_queryset_raises_the_same_type_error(async_env):
    # when
    pair = await compare(_unordered_aggregate, "afirst")
    # then
    assert pair.sync.error_type == "builtins.TypeError"
    assert pair.async_ == pair.sync


@pytest.mark.parametrize("op", ["aget", "afirst", "acount", "aexists"])
async def test_a_read_of_an_unconfigured_alias_raises_the_same_connection_does_not_exist(
    async_env, op
):
    # when
    pair = await compare(partial(Book.objects.using, "nope"), op)
    # then
    assert pair.sync.error == (
        "django.utils.connection.ConnectionDoesNotExist",
        "The connection 'nope' doesn't exist.",
    )
    assert pair.async_ == pair.sync


async def test_aget_of_a_locking_queryset_on_an_unconfigured_alias_raises_the_same_error(
    async_env, book_pk
):
    # when
    pair = await compare(lambda: Book.objects.using("nope").select_for_update(), "aget", pk=book_pk)
    # then
    assert pair.sync.error == (
        "django.utils.connection.ConnectionDoesNotExist",
        "The connection 'nope' doesn't exist.",
    )
    assert pair.async_ == pair.sync
