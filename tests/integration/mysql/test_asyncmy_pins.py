"""asyncmy behaviour the pool wrapper and the connection rely on. These pin the upper bound of the
version range (`asyncmy>=0.2.18,<0.3`): when one fails after an upgrade, the driver needs a review
first (ADR-0003 Decision 5, amendment of 2026-11-26)."""

from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import (
    asyncmy_acquire_after_close_error,
    asyncmy_connection_facts,
    asyncmy_release_in_transaction_drops_the_connection,
    probe_connection_state_after_open,
)

pytestmark = MYSQL_FAMILY


async def test_an_acquire_after_close_raises_runtime_error_mentioning_the_closing_pool(
    django_db_setup,
):
    # when
    error = await asyncmy_acquire_after_close_error()
    # then
    assert type(error) is RuntimeError
    assert "closing pool" in str(error)


async def test_releasing_a_connection_inside_a_transaction_drops_it(django_db_setup):
    # when
    before, after = await asyncmy_release_in_transaction_drops_the_connection()
    # then
    assert (before, after) == (1, 0)


async def test_open_closes_its_probe_connection_before_it_returns(django_db_setup):
    # when
    made, closed = await probe_connection_state_after_open()
    # then
    assert made == 1
    assert closed == [True]


async def test_a_bytes_statement_is_sent_without_being_encoded_again(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then: `_encode` turns the statement into bytes (surrogateescape), and asyncmy must pass them on
    assert facts.bytes_statement_hex == "E282AC"


async def test_mogrify_without_arguments_returns_the_statement_untouched(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then
    assert facts.mogrify_without_args == "SELECT '%s' /* 5% */"


async def test_the_connection_exposes_the_encoding_the_driver_encodes_with(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then
    assert isinstance(facts.encoding, str)
    assert "\u20ac".encode(facts.encoding) == b"\xe2\x82\xac"


async def test_the_server_status_flags_follow_the_session_sql_mode(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then
    assert facts.server_status_is_int is True
    assert facts.no_backslash_flag_after_set is True


async def test_literal_and_escape_string_escape_with_the_session_mode(django_db_setup):
    # when: NO_BACKSLASH_ESCAPES is on, so a backslash stays one character and a quote doubles
    facts = await asyncmy_connection_facts()
    # then
    assert facts.literal == "'a\\b'"
    assert facts.escape_string == "a''b"


async def test_mogrify_carries_the_bytes_of_a_bytes_parameter_as_surrogates_that_encode_back(
    django_db_setup,
):
    # when
    facts = await asyncmy_connection_facts()
    # then: this is why the driver checks the caller's strings instead of the formatted statement
    assert facts.mogrified_bytes_statement == b"SELECT _binary'\xff'"


async def test_the_pool_reports_size_and_freesize_as_integers(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then
    assert facts.pool_size_attribute_types == (int, int)


async def test_asyncmy_keeps_prepared_statements_off_unless_asked(django_db_setup):
    # when
    facts = await asyncmy_connection_facts()
    # then: the driver also refuses a non-zero `stmt_cache_size` (the decoder shim would be skipped)
    assert facts.stmt_cache_size_default == 0
