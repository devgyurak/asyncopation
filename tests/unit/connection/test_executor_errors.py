"""`asyncopation.ConcurrentQueryError` (ADR-0001 Decision 3): a programmer error, not a database error."""

import pytest
from django.db import DatabaseError
from django.db import Error as DjangoDbError

import asyncopation
from asyncopation import errors


def test_concurrent_query_error_is_a_runtime_error():
    # when
    parents = errors.ConcurrentQueryError.__mro__
    # then
    assert RuntimeError in parents


@pytest.mark.parametrize("database_error", [DatabaseError, DjangoDbError])
def test_concurrent_query_error_is_not_a_database_error(database_error):
    # when
    is_database_error = issubclass(errors.ConcurrentQueryError, database_error)
    # then
    assert is_database_error is False


def test_concurrent_query_error_is_reexported_at_the_package_root():
    # when
    exported = asyncopation.ConcurrentQueryError
    # then
    assert exported is errors.ConcurrentQueryError


def test_concurrent_query_error_is_listed_in_the_public_names():
    # when
    names = asyncopation.__all__
    # then
    assert "ConcurrentQueryError" in names


def test_concurrent_query_error_lives_in_the_errors_module():
    # when
    module = errors.ConcurrentQueryError.__module__
    # then
    assert module == "asyncopation.errors"


def test_concurrent_query_error_keeps_its_message():
    # when
    error = errors.ConcurrentQueryError("explained")
    # then
    assert str(error) == "explained"
