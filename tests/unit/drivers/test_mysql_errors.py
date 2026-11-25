"""asyncmy exception translation and disconnect detection (ADR-0003 Decision 4)."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import (
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

from tests.support.mysql_errors import (
    CRYPTOGRAPHY_MESSAGE,
    asyncmy_error,
    is_disconnect,
    mapped,
    translated,
    translated_class,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("DataError", DataError, id="data"),
        pytest.param("OperationalError", OperationalError, id="operational"),
        pytest.param("IntegrityError", IntegrityError, id="integrity"),
        pytest.param("InternalError", InternalError, id="internal"),
        pytest.param("ProgrammingError", ProgrammingError, id="programming"),
        pytest.param("NotSupportedError", NotSupportedError, id="not-supported"),
        pytest.param("InterfaceError", InterfaceError, id="interface"),
        pytest.param("DatabaseError", DatabaseError, id="database"),
        pytest.param("Error", Error, id="base"),
    ],
)
def test_asyncmy_errors_map_to_the_django_class_of_the_same_name(name, expected):
    # when
    result = translated_class(asyncmy_error(name, 1, "boom"))
    # then
    assert result is expected


def test_the_mapped_error_keeps_the_arguments_and_the_driver_exception_as_its_cause():
    # given
    raw = asyncmy_error("ProgrammingError", 1064, "syntax")
    # when
    result = translated(raw)
    # then
    assert result.args == (1064, "syntax")
    assert result.__cause__ is raw


@pytest.mark.parametrize("code", [1048, 1690, 3819, 4025])
def test_operational_errors_with_an_integrity_code_become_integrity_errors(code):
    # when
    result = translated_class(asyncmy_error("OperationalError", code, "constraint"))
    # then
    assert result is IntegrityError


@pytest.mark.parametrize("code", [1048, 1690, 3819, 4025])
def test_the_remapped_integrity_error_keeps_the_arguments(code):
    # when
    result = translated(asyncmy_error("OperationalError", code, "constraint"))
    # then
    assert result.args == (code, "constraint")


@pytest.mark.parametrize("code", [1205, 1213, 2013, 1040, 9999])
def test_other_operational_errors_stay_operational_errors(code):
    # when
    result = translated_class(asyncmy_error("OperationalError", code, "x"))
    # then
    assert result is OperationalError


def test_an_operational_error_without_arguments_stays_operational():
    # when
    result = translated_class(asyncmy_error("OperationalError"))
    # then
    assert result is OperationalError


def test_an_integrity_error_with_code_1062_stays_an_integrity_error():
    # when
    result = translated_class(asyncmy_error("IntegrityError", 1062, "dup"))
    # then
    assert result is IntegrityError


def test_exceptions_that_are_not_driver_errors_pass_through_unchanged():
    # given
    raw = ValueError("mine")
    # when
    result = translated(raw)
    # then
    assert result is raw


def test_a_missing_cryptography_package_is_improperly_configured():
    # when
    result = translated(RuntimeError(CRYPTOGRAPHY_MESSAGE))
    # then
    assert type(result) is ImproperlyConfigured


def test_the_improperly_configured_error_carries_an_install_hint():
    # when
    result = translated(RuntimeError(CRYPTOGRAPHY_MESSAGE))
    # then
    assert "pip install" in str(result)
    assert "cryptography" in str(result)


def test_another_runtime_error_passes_through_unchanged():
    # given
    raw = RuntimeError("something else")
    # when
    result = translated(raw)
    # then
    assert result is raw


@pytest.mark.parametrize("code", [1053, 2006, 2013, 2014, 2055, 1927, 3169])
def test_is_disconnect_is_true_for_the_connection_loss_codes(code):
    # when
    result = is_disconnect(asyncmy_error("OperationalError", code, "gone"))
    # then
    assert result is True


@pytest.mark.parametrize("code", [1053, 2006, 2013, 2014, 2055, 1927, 3169])
def test_is_disconnect_is_true_for_the_mapped_connection_loss_codes(code):
    # when
    result = is_disconnect(mapped("OperationalError", code, "gone"))
    # then
    assert result is True


@pytest.mark.parametrize(
    "args",
    [(0, "Cancelled during execution"), (0, "Already closed"), ("closed",)],
    ids=["cancelled", "already-closed", "no-code"],
)
def test_is_disconnect_is_true_for_a_closed_or_cancelled_connection(args):
    # when
    result = is_disconnect(asyncmy_error("InterfaceError", *args))
    # then
    assert result is True


def test_is_disconnect_is_true_for_a_mapped_closed_connection():
    # when
    result = is_disconnect(mapped("InterfaceError", 0, "Cancelled during execution"))
    # then
    assert result is True


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(asyncmy_error("IntegrityError", 1062, "dup"), id="raw-unique"),
        pytest.param(mapped("IntegrityError", 1062, "dup"), id="mapped-unique"),
        pytest.param(asyncmy_error("OperationalError", 1205, "lock wait"), id="raw-lock-wait"),
        pytest.param(asyncmy_error("OperationalError", 1213, "deadlock"), id="raw-deadlock"),
        pytest.param(asyncmy_error("ProgrammingError", 1064, "syntax"), id="raw-syntax"),
        pytest.param(OperationalError("pool timeout"), id="pool-timeout"),
        pytest.param(OperationalError(0, "pool timeout"), id="pool-timeout-with-code"),
        pytest.param(ValueError("x"), id="foreign"),
        pytest.param(RuntimeError("x"), id="runtime"),
    ],
)
def test_is_disconnect_is_false_for_errors_that_leave_the_connection_usable(exc):
    # when
    result = is_disconnect(exc)
    # then
    assert result is False
