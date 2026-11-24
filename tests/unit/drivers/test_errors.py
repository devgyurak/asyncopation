"""Driver exception translation.

Unknown ``psycopg.Error`` subclasses map to django's ``Error`` (Django parity); issue #2's
"DatabaseError" wording is satisfied by every DB-API error, which all derive from it.
"""

import asyncio

import psycopg
import pytest
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

from asyncopation.drivers.errors import map_exception, raise_mapped, wrap_database_errors
from tests.support.drivers import (
    custom_subclass,
    django_wrapper_class,
    instance_of,
    our_mapped_class,
    psycopg_error_classes,
)


@pytest.mark.parametrize(
    ("driver_exc", "django_exc"),
    [
        pytest.param(psycopg.errors.UniqueViolation, IntegrityError, id="unique"),
        pytest.param(psycopg.errors.NotNullViolation, IntegrityError, id="not-null"),
        pytest.param(psycopg.errors.CheckViolation, IntegrityError, id="check"),
        pytest.param(psycopg.errors.ForeignKeyViolation, IntegrityError, id="foreign-key"),
        pytest.param(psycopg.errors.ExclusionViolation, IntegrityError, id="exclusion"),
        pytest.param(psycopg.errors.SerializationFailure, OperationalError, id="serialization"),
        pytest.param(psycopg.errors.DeadlockDetected, OperationalError, id="deadlock"),
        pytest.param(psycopg.errors.QueryCanceled, OperationalError, id="query-canceled"),
        pytest.param(psycopg.errors.DivisionByZero, DataError, id="data-subclass"),
        pytest.param(psycopg.errors.AdminShutdown, OperationalError, id="operational-subclass"),
        pytest.param(psycopg.errors.SyntaxError, ProgrammingError, id="programming-subclass"),
        pytest.param(psycopg.errors.FeatureNotSupported, NotSupportedError, id="not-supported"),
        pytest.param(psycopg.errors.InternalError_, InternalError, id="internal"),
        pytest.param(psycopg.InterfaceError, InterfaceError, id="interface"),
        pytest.param(psycopg.DatabaseError, DatabaseError, id="database"),
        pytest.param(psycopg.Error, Error, id="base"),
    ],
)
def test_map_exception_returns_most_specific_django_class(driver_exc, django_exc):
    # given
    exc = instance_of(driver_exc, "boom")
    # when
    mapped = map_exception(exc, psycopg)
    # then
    assert type(mapped) is django_exc


def test_map_exception_maps_unknown_subclass_by_its_closest_base():
    # given
    exc = instance_of(custom_subclass(psycopg.IntegrityError), "boom")
    # when
    mapped = map_exception(exc, psycopg)
    # then
    assert type(mapped) is IntegrityError


def test_map_exception_copies_args():
    # given
    exc = psycopg.errors.UniqueViolation("duplicate key", "second")
    # when
    mapped = map_exception(exc, psycopg)
    # then
    assert mapped is not None
    assert mapped.args == ("duplicate key", "second")


@pytest.mark.parametrize(
    "exc",
    [ValueError("x"), KeyError("k"), asyncio.CancelledError(), TimeoutError()],
    ids=["value-error", "key-error", "cancelled", "timeout"],
)
def test_map_exception_ignores_non_driver_exceptions(exc):
    # when
    mapped = map_exception(exc, psycopg)
    # then
    assert mapped is None


def test_raise_mapped_chains_the_driver_exception_as_cause():
    # given
    original = psycopg.errors.UniqueViolation("dup")
    # when / then
    with pytest.raises(IntegrityError) as caught:
        raise_mapped(original, psycopg)
    assert caught.value.__cause__ is original


def test_raise_mapped_reraises_foreign_exception_unchanged():
    # given
    original = ValueError("x")
    # when / then
    with pytest.raises(ValueError, match="x") as caught:
        raise_mapped(original, psycopg)
    assert caught.value is original


def test_wrap_database_errors_translates_driver_errors():
    # when / then
    with pytest.raises(DataError) as caught, wrap_database_errors(psycopg):
        raise psycopg.errors.DivisionByZero("x")
    assert isinstance(caught.value.__cause__, psycopg.errors.DivisionByZero)


def test_wrap_database_errors_passes_foreign_exceptions_through():
    # given
    original = ValueError("x")
    # when / then
    with pytest.raises(ValueError, match="x") as caught, wrap_database_errors(psycopg):
        raise original
    assert caught.value is original


def test_wrap_database_errors_does_not_touch_cancellation():
    # given
    original = asyncio.CancelledError()
    # when / then
    with pytest.raises(asyncio.CancelledError) as caught, wrap_database_errors(psycopg):
        raise original
    assert caught.value is original


def test_wrap_database_errors_is_transparent_without_errors():
    # when
    with wrap_database_errors(psycopg):
        value = 1 + 1
    # then
    assert value == 2


@pytest.mark.parametrize("exc_class", psycopg_error_classes(), ids=lambda c: c.__name__)
def test_mapping_matches_djangos_database_error_wrapper(exc_class):
    # given
    exc = instance_of(exc_class, "boom")
    # when
    ours = our_mapped_class(exc)
    # then
    assert ours is django_wrapper_class(exc)
