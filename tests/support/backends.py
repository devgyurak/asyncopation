"""Backend gates expressed as markers (never as control flow in test modules)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from harness.settings import VENDOR

POSTGRESQL_ONLY = pytest.mark.skipif(
    VENDOR != "postgresql", reason="the asyncmy driver arrives with issue #7"
)

# Before #7 nothing opens a connection or compiles on MySQL/MariaDB, so these would pass trivially
# there (and strict xfail cannot be used: they do not fail).
TRIVIAL_BEFORE_ASYNCMY = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="before asyncmy (#7) nothing opens or compiles on MySQL/MariaDB: the assertion is vacuous",
)

# MySQL/MariaDB execution lands with #7; strict so the marker is removed when it does.
# Until then the registry has no engine for those backends, so the first open raises
# `ImproperlyConfigured` ("not supported yet"); tests that capture errors into a differential
# outcome see that as a mismatch, an `AssertionError`. Anything else (a driver import error, a
# TypeError in the wrapper) is a real failure and is not xfailed.
# Only for tests that open a driver: a test whose error is raised before any driver opens (an
# unconfigured alias, an argument error of `aget` / `afirst`) passes on MySQL/MariaDB, would XPASS
# under this strict marker and so lives in a module without it (`test_read_api_before_io.py`).
# The suites have been run on MySQL and MariaDB (with `mysqlclient` built locally for the sync
# side), where these tests xfail.
XFAIL_UNTIL_ASYNCMY = pytest.mark.xfail(
    VENDOR != "postgresql",
    raises=(AssertionError, ImproperlyConfigured),
    strict=True,
    reason="MySQL/MariaDB async execution arrives with issue #7 (asyncmy driver)",
)
