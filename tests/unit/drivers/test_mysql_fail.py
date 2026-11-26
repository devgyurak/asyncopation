"""`MySQLConnection._fail`: only a server-reported error leaves the protocol in a known state."""

import pytest
from django.db import (
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

from tests.support.mysql_fakes import asyncmy_error, fail_with


@pytest.mark.parametrize(
    ("exc", "django_class"),
    [
        pytest.param(
            asyncmy_error("InternalError", 0, "Packet sequence number wrong"),
            InternalError,
            id="internal",
        ),
        pytest.param(
            asyncmy_error("OperationalError", 2013, "lost"), OperationalError, id="disconnect"
        ),
        pytest.param(
            asyncmy_error("OperationalError", 2014, "Commands out of sync"),
            ProgrammingError,
            id="commands-out-of-sync",
        ),
        pytest.param(
            asyncmy_error("InterfaceError", 0, "Cancelled during execution"),
            InterfaceError,
            id="interface",
        ),
    ],
)
async def test_internal_errors_and_disconnects_discard_the_connection(exc, django_class):
    # when
    raised, discarded = await fail_with(exc)
    # then
    assert discarded is True
    assert type(raised) is django_class


async def test_a_foreign_exception_discards_the_connection_and_propagates_unchanged():
    # given
    exc = ValueError("decoder failed mid result")
    # when
    raised, discarded = await fail_with(exc)
    # then
    assert raised is exc
    assert discarded is True


@pytest.mark.parametrize(
    ("exc", "django_class"),
    [
        pytest.param(asyncmy_error("IntegrityError", 1062, "dup"), IntegrityError, id="unique"),
        pytest.param(
            asyncmy_error("OperationalError", 1205, "lock wait"), OperationalError, id="lock-wait"
        ),
    ],
)
async def test_a_server_reported_error_keeps_the_connection(exc, django_class):
    # when
    raised, discarded = await fail_with(exc)
    # then
    assert discarded is False
    assert type(raised) is django_class
