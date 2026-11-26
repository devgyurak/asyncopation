"""The MySQL wrapper drift suite never skips silently where CI requires it to run."""

from tests.support.mysql_drift import current_requirement_violation, requirement_violation


def test_mysqldb_is_installed_wherever_the_drift_suite_is_required():
    # when
    violation = current_requirement_violation()
    # then
    assert violation is None


def test_a_required_but_missing_mysqldb_is_a_violation():
    # when
    violation = requirement_violation({"ASYNCOPATION_REQUIRE_MYSQLDB": "1"}, available=False)
    # then
    assert violation == "ASYNCOPATION_REQUIRE_MYSQLDB=1 but MySQLdb is not importable"


def test_a_missing_mysqldb_is_fine_when_it_is_not_required():
    # when
    violation = requirement_violation({}, available=False)
    # then
    assert violation is None


def test_an_installed_mysqldb_is_fine_when_it_is_required():
    # when
    violation = requirement_violation({"ASYNCOPATION_REQUIRE_MYSQLDB": "1"}, available=True)
    # then
    assert violation is None


def test_a_requirement_value_other_than_one_does_not_require_it():
    # when
    violation = requirement_violation({"ASYNCOPATION_REQUIRE_MYSQLDB": "0"}, available=False)
    # then
    assert violation is None
