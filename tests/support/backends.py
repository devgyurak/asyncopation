"""Backend gates expressed as markers (never as control flow in test modules)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from harness.settings import VENDOR

POSTGRESQL_ONLY = pytest.mark.skipif(
    VENDOR != "postgresql", reason="the asyncmy driver arrives with issue #7"
)

# MySQL/MariaDB execution lands with #7; strict so the marker is removed when it does.
# Until then the registry has no engine for those backends, so the first open raises
# `ImproperlyConfigured` ("not supported yet"); tests that capture errors into a differential
# outcome see that as a mismatch, an `AssertionError`. Anything else (a driver import error, a
# TypeError in the wrapper) is a real failure and is not xfailed. Derived from the code, not run
# against MySQL/MariaDB locally.
XFAIL_UNTIL_ASYNCMY = pytest.mark.xfail(
    VENDOR != "postgresql",
    raises=(AssertionError, ImproperlyConfigured),
    strict=True,
    reason="MySQL/MariaDB async execution arrives with issue #7 (asyncmy driver)",
)
