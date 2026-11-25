"""Backend gates expressed as markers (never as control flow in test modules)."""

from __future__ import annotations

import pytest

from harness.settings import VENDOR

POSTGRESQL_ONLY = pytest.mark.skipif(
    VENDOR != "postgresql", reason="the asyncmy driver arrives with issue #7"
)

# MySQL/MariaDB execution lands with #7; strict so the marker is removed when it does.
XFAIL_UNTIL_ASYNCMY = pytest.mark.xfail(
    VENDOR != "postgresql",
    strict=True,
    reason="MySQL/MariaDB async execution arrives with issue #7 (asyncmy driver)",
)
