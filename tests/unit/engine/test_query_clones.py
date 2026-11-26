"""The QuerySets `aget` and a querying `afirst` build are FETCH_RAISE over a shared filled cache."""

from django.db.models import (
    FETCH_PEERS,  # ty: ignore[missing-module-member] - missing from django-types
    FETCH_RAISE,  # ty: ignore[missing-module-member] - missing from django-types
)

from asyncopation._django import query as dq
from harness.catalog.models import Book, Order
from tests.support.query_clones import filled_in_a_user_fetch_mode


def test_get_clone_of_a_queryset_filled_in_a_user_fetch_mode_is_fetch_raise():
    # given
    shared = filled_in_a_user_fetch_mode(Book.objects.all())
    # when
    clone = dq.get_clone(shared, (), {"pk": 1})
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(clone)) == (
        FETCH_PEERS,
        FETCH_RAISE,
    )


def test_get_clone_is_not_the_shared_queryset():
    # given
    shared = filled_in_a_user_fetch_mode(Book.objects.all())
    # when
    clone = dq.get_clone(shared, (), {"pk": 1})
    # then
    assert clone is not shared


def test_get_clone_after_a_combinator_is_fetch_raise():
    # given
    shared = Book.objects.all().union(Book.objects.all())
    dq.set_fetch_mode(shared, FETCH_PEERS)
    # when
    clone = dq.get_clone(shared, (), {})
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(clone)) == (
        FETCH_PEERS,
        FETCH_RAISE,
    )


def test_get_clone_after_a_combinator_is_not_the_shared_queryset():
    # given
    shared = Book.objects.all().union(Book.objects.all())
    # when
    clone = dq.get_clone(shared, (), {})
    # then
    assert clone is not shared


def test_first_queryset_of_a_queryset_without_ordering_is_fetch_raise():
    # given: Order has no Meta ordering, so a plain QuerySet is unordered with default_ordering on
    shared = filled_in_a_user_fetch_mode(Order.objects.all())
    # when
    ordered = dq.first_queryset(shared)
    # then
    assert (dq.queryset_fetch_mode(shared), dq.queryset_fetch_mode(ordered)) == (
        FETCH_PEERS,
        FETCH_RAISE,
    )


def test_first_queryset_of_an_unordered_queryset_is_not_the_shared_queryset():
    # given
    shared = filled_in_a_user_fetch_mode(Order.objects.all())
    # when
    ordered = dq.first_queryset(shared)
    # then
    assert ordered is not shared


def test_first_queryset_of_an_ordered_queryset_is_returned_unchanged():
    # given
    shared = filled_in_a_user_fetch_mode(Book.objects.order_by("pk"))
    # when
    same = dq.first_queryset(shared)
    # then
    assert (same is shared, dq.queryset_fetch_mode(same)) == (True, FETCH_PEERS)


def test_first_queryset_of_a_queryset_without_default_ordering_is_returned_unchanged():
    # given: `order_by()` sets default_ordering False, the branch where `first()` keeps the query
    shared = filled_in_a_user_fetch_mode(Book.objects.order_by())
    # when
    same = dq.first_queryset(shared)
    # then
    assert (same is shared, dq.queryset_fetch_mode(same)) == (True, FETCH_PEERS)
