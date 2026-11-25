"""A101 lets exactly `TransactionManagementError` through, and only inside `_django/` (#5)."""

import pytest

from tests.support.guards import distinct_codes, has_violation

P = pytest.param
DJANGO = "src/asyncopation/_django/transaction.py"
ELSEWHERE = "src/asyncopation/connection/executor.py"
MODULE = "django.db.transaction"
ERROR = "from django.db.transaction import TransactionManagementError\n"


def test_the_error_class_may_be_imported_inside_the_django_package():
    # given
    source = ERROR
    # when
    codes = distinct_codes(source, DJANGO)
    # then
    assert "A101" not in codes


def test_the_error_class_is_still_banned_outside_the_django_package():
    # given
    source = ERROR
    # when
    banned = has_violation(source, ELSEWHERE, "A101", MODULE)
    # then
    assert banned is True


def test_an_aliased_import_of_the_error_is_still_banned_outside_the_django_package():
    # given
    source = "from django.db.transaction import TransactionManagementError as Failure\n"
    # when
    banned = has_violation(source, ELSEWHERE, "A101", MODULE)
    # then
    assert banned is True


def test_an_aliased_import_of_the_error_is_allowed_inside_the_django_package():
    # given
    source = "from django.db.transaction import TransactionManagementError as Failure\n"
    # when
    codes = distinct_codes(source, DJANGO)
    # then
    assert "A101" not in codes


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        P("from django.db.transaction import atomic\n", ["A101", "A201"], id="atomic"),
        P("import django.db.transaction\n", ["A101", "A201"], id="module"),
        P(
            "from django.db.transaction import TransactionManagementError, atomic\n",
            ["A101", "A201"],
            id="mixed",
        ),
        P("from django.db import transaction\n", ["A102"], id="package-attribute"),
    ],
)
def test_the_rest_of_the_transaction_module_stays_banned_inside_the_django_package(
    source, expected
):
    # when
    codes = distinct_codes(source, DJANGO)
    # then
    assert codes == expected
