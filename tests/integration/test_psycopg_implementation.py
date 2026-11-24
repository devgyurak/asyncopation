"""`make test-compose-python` really runs the pure-Python psycopg (no silent fallback)."""

import pytest

from tests.support.psycopg_impl import loaded_implementation, requested_implementation

pytestmark = pytest.mark.skipif(
    requested_implementation() is None, reason="PSYCOPG_IMPL is not set"
)


def test_the_requested_psycopg_implementation_is_the_one_loaded():
    # when
    loaded = loaded_implementation()
    # then
    assert loaded == requested_implementation()
