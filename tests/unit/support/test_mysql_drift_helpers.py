"""The MySQL drift helpers report what failed, not just that something did."""

from tests.support.mysql_drift import observe


def test_observe_records_the_type_of_the_exception_a_read_raised():
    # when
    outcome = observe(lambda: 1 // 0)
    # then
    assert outcome == ("raises", "ZeroDivisionError")


def test_observe_records_a_value_that_was_read():
    # when
    outcome = observe(lambda: 7)
    # then
    assert outcome == ("value", 7)
