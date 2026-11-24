"""`make test-compose-python` really runs the expected psycopg implementation (no silent fallback)."""

import pytest

from tests.support.psycopg_impl import expected_implementation, loaded_implementation

pytestmark = pytest.mark.skipif(
    expected_implementation() is None, reason="ASYNCOPATION_EXPECT_PSYCOPG_IMPL is not set"
)


def test_the_expected_psycopg_implementation_is_the_one_loaded():
    # when
    loaded = loaded_implementation()
    # then
    assert loaded == expected_implementation()
