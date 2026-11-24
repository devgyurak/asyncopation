from decimal import Decimal

import pytest

from harness.catalog.models import Book
from tests.support.snapshot import annotation_names, snapshot, snapshot_result
from tests.support.snapshot_graphs import (
    cached_related,
    make_author,
    make_book,
    make_one_to_one_cycle,
    make_publisher,
)


def test_equal_values_with_different_types_produce_different_snapshots():
    # given
    as_decimal, as_float = [{"price": Decimal("1.5")}], [{"price": 1.5}]
    # when
    left, right = snapshot_result(as_decimal), snapshot_result(as_float)
    # then
    assert left != right


@pytest.mark.parametrize(
    ("left", "right"),
    [([{"a": 1, "b": 2}], [{"b": 2, "a": 1}]), ([b"x"], [b"x"]), (None, None)],
    ids=["dict-key-order", "bytes", "none"],
)
def test_equal_results_produce_equal_snapshots(left, right):
    # when
    result = snapshot_result(left) == snapshot_result(right)
    # then
    assert result


def test_row_order_is_part_of_the_snapshot():
    # when
    result = snapshot_result([1, 2]) == snapshot_result([2, 1])
    # then
    assert result is False


def test_annotation_values_are_part_of_an_instance_snapshot():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(n_tags=1)
    vars(right).update(n_tags=2)
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_annotation_names_lists_public_non_field_attributes():
    # given
    book = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(book).update(n_tags=3, _private="ignored")
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ("n_tags",)


def test_annotation_with_equal_value_but_different_type_differs():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(total=Decimal("1"))
    vars(right).update(total=1)
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_instance_without_annotations_has_empty_annotation_names():
    # given
    book = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ()


def test_to_attr_list_snapshots_compare_instance_fields():
    # given
    left = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    right = Book(pk=1, title="t", pages=1, price=Decimal("1.00"))
    vars(left).update(siblings=[Book(pk=2, title="a", pages=1, price=Decimal("1.00"))])
    vars(right).update(siblings=[Book(pk=2, title="b", pages=1, price=Decimal("1.00"))])
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_related_object_field_value_is_part_of_the_snapshot():
    # given
    left = make_book(make_author("x"))
    right = make_book(make_author("y"))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_related_object_annotation_type_difference_is_detected():
    # given
    left_author, right_author = make_author(), make_author()
    vars(left_author).update(n=Decimal("1"))
    vars(right_author).update(n=1)
    # when
    result = snapshot(make_book(left_author)) == snapshot(make_book(right_author))
    # then
    assert result is False


def test_loaded_relation_differs_from_unloaded_relation():
    # given
    loaded, unloaded = make_book(make_author()), make_book()
    # when
    result = snapshot(loaded) == snapshot(unloaded)
    # then
    assert result is False


@pytest.mark.parametrize(
    ("loaded_as_none", "unloaded"),
    [
        (make_author(publisher=None), make_author()),
        (make_author(profile=None), make_author()),
    ],
    ids=["nullable-fk", "missing-reverse-one-to-one"],
)
def test_relation_loaded_as_none_differs_from_unloaded_relation(loaded_as_none, unloaded):
    # when
    result = snapshot(loaded_as_none) == snapshot(unloaded)
    # then
    assert result is False


def test_nested_relation_difference_is_detected():
    # given
    left = make_book(make_author(publisher=make_publisher("x")))
    right = make_book(make_author(publisher=make_publisher("y")))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_equal_related_graphs_produce_equal_snapshots():
    # given
    left = make_book(make_author(publisher=make_publisher(), profile=None))
    right = make_book(make_author(publisher=make_publisher(), profile=None))
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result


def test_related_entries_are_sorted_by_cache_name():
    # given
    author = make_author(publisher=None, profile=None)
    # when
    related = snapshot(author)[3]
    # then
    assert related == (("profile", None), ("publisher", None))


def test_one_to_one_cycle_terminates_with_back_reference():
    # given
    author = make_one_to_one_cycle()
    # when
    related = snapshot(author)[3]
    # then
    assert related[0][1][3] == (("author", ("<ref>", "catalog.Author", ("int", 1))),)


def test_one_to_one_cycle_snapshots_are_equal_for_equal_graphs():
    # given
    left, right = make_one_to_one_cycle(), make_one_to_one_cycle()
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result


def test_one_to_one_cycle_differs_when_profile_differs():
    # given
    left = make_one_to_one_cycle()
    right = make_one_to_one_cycle()
    cached_related(right, "profile").bio = "other"
    # when
    result = snapshot(left) == snapshot(right)
    # then
    assert result is False


def test_annotation_names_ignores_related_objects():
    # given
    book = make_book(make_author())
    # when
    names = annotation_names(snapshot(book))
    # then
    assert names == ()


def test_instance_cycle_through_an_annotation_terminates():
    # given
    author = make_author()
    vars(author).update(self_ref=author)
    # when
    related = snapshot(author)[2]
    # then
    assert related == (("self_ref", "Author", ("<ref>", "catalog.Author", ("int", 1))),)
