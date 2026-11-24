from decimal import Decimal

import pytest

from tests.support.snapshot import snapshot_result


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
