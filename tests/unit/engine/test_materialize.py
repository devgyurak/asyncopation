"""`materialize` builds the same results as Django's own iterables from the same fixed rows."""

import pytest

from tests.support.engine.catalog import known_author
from tests.support.engine.stages import (
    access_error,
    attribute_values,
    cached_related,
    django_materialized,
    materialized,
    none_flags,
    peer_shape,
    same_object_flags,
    snapshots,
)

CASES = [
    "book_all",
    "book_select_related_author",
    "book_annotated",
    "book_only_title",
    "author_select_related_publisher",
    "values",
    "values_list",
    "values_list_flat",
    "values_list_named",
    "author_books",
    "author_books_select_related",
    "author_books_only_title",
]


@pytest.mark.parametrize("name", CASES)
def test_materialize_equals_the_django_iterable_on_the_same_rows(alias, name):
    # when
    ours, django = snapshots(name, alias)
    # then
    assert ours == django


@pytest.mark.parametrize(
    ("name", "count"),
    [("book_all", 3), ("values", 2), ("values_list_flat", 2), ("values_list_named", 2)],
)
def test_materialize_returns_one_item_per_row(alias, name, count):
    # when
    result = materialized(name, alias)
    # then
    assert len(result) == count


def test_materialize_sets_annotation_columns_on_the_instances(alias):
    # when
    result = materialized("book_annotated", alias)
    # then
    assert attribute_values(result, "double_pages") == [240, 0, 1998]


def test_materialize_populates_select_related_objects(alias):
    # when
    result = materialized("book_select_related_author", alias)
    # then
    assert attribute_values(attribute_values(result, "author"), "name") == ["ann", "bob", "ann"]


def test_materialize_caches_a_null_select_related_as_none(alias):
    # when
    result = materialized("author_select_related_publisher", alias)
    # then
    assert none_flags(attribute_values(result, "publisher")) == [False, True]
    assert "publisher" in result[1]._state.fields_cache


def test_materialize_applies_field_converters(alias):
    # when
    result = materialized("book_all", alias)
    # then
    assert attribute_values(result, "metadata") == [{"k": 1}, {}, [1, 2]]


def test_materialize_shares_one_peer_list_under_fetch_peers(alias):
    # when
    ours, django = (
        peer_shape(materialized("book_peers", alias)),
        peer_shape(django_materialized("book_peers", alias)),
    )
    # then
    assert ours == django == (1, 3, True)


def test_materialize_has_no_peers_under_the_default_fetch_mode(alias):
    # when
    shape = peer_shape(materialized("book_all", alias))
    # then
    assert shape == peer_shape(django_materialized("book_all", alias))
    assert shape == (1, 0, False)


def test_materialize_blocks_deferred_access_under_fetch_raise(alias):
    # when
    ours = access_error(materialized("book_only_raise", alias), "price")
    django = access_error(django_materialized("book_only_raise", alias), "price")
    # then
    assert ours == django == "FieldFetchBlocked"


def test_materialize_keeps_loaded_fields_readable_under_fetch_raise(alias):
    # when
    result = materialized("book_only_raise", alias)
    # then
    assert attribute_values(result, "title") == ["alpha", "beta"]


def test_materialize_sets_the_known_related_object_of_a_related_manager_queryset(alias):
    # when
    result = materialized("author_books", alias)
    # then
    assert same_object_flags(cached_related(result, "author"), known_author(alias)) == [
        True,
        False,
        True,
    ]


def test_materialize_leaves_the_relation_uncached_for_rows_of_another_parent(alias):
    # when
    result = materialized("author_books", alias)
    # then
    assert none_flags(cached_related(result, "author")) == [False, True, False]


def test_materialize_prefers_the_select_related_object_over_the_known_related_object(alias):
    # when
    result = materialized("author_books_select_related", alias)
    # then
    assert same_object_flags(cached_related(result, "author"), known_author(alias)) == [False] * 3
    assert attribute_values(cached_related(result, "author"), "name") == ["ann", "bob", "ann"]


def test_materialize_skips_the_known_related_object_when_the_fk_attname_is_deferred(alias):
    # when
    result = materialized("author_books_only_title", alias)
    # then
    assert none_flags(cached_related(result, "author")) == [True, True]
