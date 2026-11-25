"""Backend gates expressed as markers (never as control flow in test modules)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from hypothesis.errors import FlakyFailure

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
POSTGRESQL_SQL_ONLY = pytest.mark.skipif(
    VENDOR != "postgresql", reason="PostgreSQL-only SQL (django.contrib.postgres full-text search)"
)

MYSQL_FAMILY_ONLY = pytest.mark.skipif(VENDOR == "postgresql", reason="MySQL/MariaDB-specific SQL")
MYSQL_ONLY = pytest.mark.skipif(VENDOR != "mysql", reason="MySQL-specific SQL")
MARIADB_ONLY = pytest.mark.skipif(VENDOR != "mariadb", reason="MariaDB-specific SQL")
# MySQL cannot select from the table it updates (`update_can_self_select`): Django runs the
# sub-select of an UPDATE through a join while compiling, which needs a connection.
SELF_SELECT_UPDATE_ONLY = pytest.mark.skipif(
    VENDOR == "mysql", reason="MySQL runs a pre-select query while compiling a joined UPDATE"
)

# MySQL/MariaDB read path: #7a registered the asyncmy engine, so most of the tests behind this
# marker pass on MySQL/MariaDB now, and a few still fail because their helpers or expectations are
# PostgreSQL-specific (a `pg_sleep` cancel probe, an UPDATE/count shape MySQL rejects). #7b ports
# them and removes the marker. It is not strict meanwhile (a strict one would fail on the passing
# tests); only the failures of an assertion are tolerated, any other error is a real failure.
XFAIL_UNTIL_ASYNCMY = pytest.mark.xfail(
    VENDOR != "postgresql",
    raises=(AssertionError, ImproperlyConfigured, FlakyFailure),  # a non-deterministic mismatch
    strict=False,
    reason="MySQL/MariaDB read-path differential is completed in #7b",
)
