"""Fixtures for the tests of `tests/support/lifecycle` itself (shared with the connection tests)."""

from tests.unit.connection import conftest as lifecycle_fixtures

fake = lifecycle_fixtures.fake
configured = lifecycle_fixtures.configured
opened = lifecycle_fixtures.opened
opening = lifecycle_fixtures.opening
