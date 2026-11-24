from decimal import Decimal

import pytest

from harness.catalog.models import Book
from tests.support.snapshot import annotation_names, snapshot, snapshot_result


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
