"""The QuerySets `aget` and a querying `afirst` build are FETCH_RAISE over a shared filled cache."""

from django.db.models import (
    FETCH_ONE,  # ty: ignore[missing-module-member] - missing from django-types
    FETCH_RAISE,  # ty: ignore[missing-module-member] - missing from django-types
)

from asyncopation._django import query as dq
from harness.catalog.models import Book, Order


def _filled(queryset):
    dq.set_cached_result(queryset, [])
    return queryset


def test_get_clone_of_a_queryset_filled_in_a_user_fetch_mode_is_fetch_raise():
    # given
    shared = _filled(Book.objects.all())
    # when
    clone = dq.get_clone(shared, (), {"pk": 1})
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(clone)) == (
        FETCH_ONE,
        FETCH_RAISE,
    )


def test_get_clone_after_a_combinator_is_fetch_raise():
    # given
    shared = Book.objects.all().union(Book.objects.all())
    # when
    clone = dq.get_clone(shared, (), {})
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(clone)) == (
        FETCH_ONE,
        FETCH_RAISE,
    )


def test_first_queryset_of_an_unordered_queryset_is_fetch_raise():
    # given
    shared = _filled(Order.objects.all())
    # when
    ordered = dq.first_queryset(shared)
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(ordered)) == (
        FETCH_ONE,
        FETCH_RAISE,
    )


def test_first_queryset_of_an_ordered_queryset_is_returned_unchanged():
    # given
    shared = _filled(Book.objects.all())
    # when
    same = dq.first_queryset(shared)
    # then
    assert (same is shared, dq.queryset_fetch_mode(same)) == (True, FETCH_ONE)
