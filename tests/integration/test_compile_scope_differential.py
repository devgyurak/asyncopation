"""Compile-only wrapper inside compile_scope generates the same SQL as Django's real wrapper.

Runs on PostgreSQL, MySQL and MariaDB (the SearchHeadline tests on PostgreSQL only).

TODO(#6): materialization outside the scope is verified when the executor lands.
"""

from functools import partial

import pytest
from hypothesis import given

from tests.support.backends import (
    MARIADB_ONLY,
    MYSQL_FAMILY_ONLY,
    MYSQL_ONLY,
    POSTGRESQL_SQL_ONLY,
    SELF_SELECT_UPDATE_ONLY,
)
from tests.support.compile_cases import (
    aware_datetime_filter,
    binary_filter,
    binary_insert,
    binary_update,
    bulk_insert_ignore_conflicts,
    bulk_insert_update_conflicts,
    cast_pages_to_char,
    cast_pages_to_uuid,
    compare_after_timezone_override,
    compare_aware_lookup_after_naive_override,
    compare_under_timezone_override,
    decimal_filter,
    duration_filter,
    exists_filter,
    json_contained_by_filter,
    json_contains_filter,
    json_has_key_filter,
    metadata_key_lookup,
    metadata_nested_key_lookup,
    published_date_lookup,
    published_extract_year,
    published_trunc_date,
    subquery_filter,
    timezone_names_after_naive_override,
    title_contains_author_name,
    title_contains_literal_with_wildcards,
    title_endswith_author_name,
    title_icontains_author_name,
    title_iendswith_author_name,
    title_iexact,
    title_iregex,
    title_istartswith_author_name,
    title_regex,
    title_startswith_author_name,
    update_filtered_through_a_join,
    uuid_filter,
)
from tests.support.compile_differential import (
    annotated_books,
    compare_compilation,
    distinct_books,
    exists_of,
    filtered_books,
    for_update_of_query,
    for_update_of_related_query,
    for_update_of_skip_locked_query,
    for_update_query,
    for_update_skip_locked_query,
    for_update_sliced_query,
    outer_of,
    plain_books,
    query_of,
    sliced_books,
    union_books,
)
from tests.support.search_queries import (
    HEADLINE_COMPOSED_PARAMS,
    HEADLINE_OPTION_PARAMS,
    headline_query,
)
from tests.support.strategies import queryset_specs

COUNTED = pytest.mark.parametrize(
    "build",
    [plain_books, filtered_books, sliced_books, distinct_books, annotated_books, union_books],
    ids=["plain", "filtered", "sliced", "distinct", "annotated", "union"],
)


@given(spec=queryset_specs())
def test_generated_querysets_compile_identically(readonly_db, spec):
    # when
    result = compare_compilation(partial(query_of, spec))
    # then
    assert result.verdict == result.expected


@COUNTED
def test_count_queries_compile_identically(readonly_db, build):
    # when
    result = compare_compilation(partial(outer_of, build))
    # then
    assert result.verdict == result.expected


@pytest.mark.parametrize(
    "build",
    [plain_books, sliced_books, distinct_books, union_books],
    ids=["plain", "sliced", "distinct", "union"],
)
def test_exists_queries_compile_identically(readonly_db, build):
    # when
    result = compare_compilation(partial(exists_of, build))
    # then
    assert result.verdict == result.expected


@pytest.mark.parametrize(
    "build",
    [
        for_update_query,
        for_update_of_query,
        for_update_skip_locked_query,
        for_update_of_skip_locked_query,
        for_update_sliced_query,
        for_update_of_related_query,
    ],
    ids=["plain", "of-nowait", "skip-locked", "of-skip-locked", "sliced", "of-related"],
)
@pytest.mark.parametrize("autocommit", [True, False], ids=["autocommit", "in-transaction"])
def test_select_for_update_compiles_identically(readonly_db, build, autocommit):
    # when
    result = compare_compilation(build, autocommit=autocommit)
    # then
    assert result.verdict == result.expected


def test_select_for_update_outside_a_transaction_raises_like_django(readonly_db):
    # given
    expected = ("raises", "TransactionManagementError")
    # when
    result = compare_compilation(for_update_query, autocommit=True)
    # then
    assert (result.real, result.only) == (expected, expected)


def test_select_for_update_skip_locked_in_a_transaction_compiles_to_sql(readonly_db):
    # when
    result = compare_compilation(for_update_skip_locked_query, autocommit=False)
    # then
    assert (result.verdict, result.only[0], "SKIP LOCKED" in result.only[1]) == (
        result.expected,
        "sql",
        True,
    )


@MYSQL_ONLY
def test_select_for_update_of_compiles_on_mysql(readonly_db):
    # when
    result = compare_compilation(for_update_of_query, autocommit=False)
    # then
    assert (result.verdict, result.only[0], " OF " in result.only[1]) == (
        result.expected,
        "sql",
        True,
    )


@MARIADB_ONLY
def test_select_for_update_of_is_not_supported_on_mariadb_in_either_wrapper(readonly_db):
    # given
    expected = ("raises", "NotSupportedError")
    # when
    result = compare_compilation(for_update_of_query, autocommit=False)
    # then
    assert (result.real, result.only) == (expected, expected)


@POSTGRESQL_SQL_ONLY
@pytest.mark.parametrize("options", HEADLINE_OPTION_PARAMS)
def test_search_headline_options_compile_identically(readonly_db, options):
    # when
    result = compare_compilation(partial(headline_query, options))
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


@POSTGRESQL_SQL_ONLY
@pytest.mark.parametrize(("options", "composed"), HEADLINE_COMPOSED_PARAMS)
def test_search_headline_options_are_composed_into_the_parameters(readonly_db, options, composed):
    # when
    result = compare_compilation(partial(headline_query, options))
    # then
    assert composed in result.only[2]


@pytest.mark.parametrize(
    "build",
    [
        title_contains_author_name,
        title_icontains_author_name,
        title_startswith_author_name,
        title_endswith_author_name,
        title_istartswith_author_name,
        title_iendswith_author_name,
        title_contains_literal_with_wildcards,
        title_regex,
        title_iregex,
        title_iexact,
        published_date_lookup,
        published_trunc_date,
        published_extract_year,
        metadata_key_lookup,
        metadata_nested_key_lookup,
        cast_pages_to_char,
        cast_pages_to_uuid,
        subquery_filter,
        exists_filter,
        uuid_filter,
        aware_datetime_filter,
        decimal_filter,
        duration_filter,
        json_contains_filter,
        json_has_key_filter,
        json_contained_by_filter,
        bulk_insert_ignore_conflicts,
        bulk_insert_update_conflicts,
    ],
    ids=[
        "contains-expression",
        "icontains-expression",
        "startswith-expression",
        "endswith-expression",
        "istartswith-expression",
        "iendswith-expression",
        "contains-wildcards",
        "regex",
        "iregex",
        "iexact",
        "date-lookup",
        "trunc-date",
        "extract-year",
        "json-key",
        "json-nested-key",
        "cast",
        "cast-uuid",
        "subquery",
        "exists",
        "uuid",
        "aware-datetime",
        "decimal",
        "duration",
        "json-contains",
        "json-has-key",
        "json-contained-by",
        "bulk-insert-ignore-conflicts",
        "bulk-insert-update-conflicts",
    ],
)
def test_backend_sensitive_lookups_compile_identically(readonly_db, build):
    # when
    result = compare_compilation(build)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


@pytest.mark.parametrize(
    "build",
    [binary_filter, binary_insert, binary_update],
    ids=["filter", "insert", "update"],
)
def test_bytes_values_compile_identically(readonly_db, build):
    # when
    result = compare_compilation(build)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


@MYSQL_FAMILY_ONLY
def test_bytes_values_reach_the_parameters_as_bytes(readonly_db):
    # when
    result = compare_compilation(binary_filter)
    # then
    assert (result.only[2], result.only[3]) == ((b"abc",), (bytes,))


def test_a_timezone_override_compiles_identically_to_the_real_wrapper(readonly_db):
    # when
    result = compare_under_timezone_override(published_date_lookup)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


def test_a_timezone_override_reaches_the_generated_sql(readonly_db):
    # when
    result = compare_under_timezone_override(published_date_lookup)
    # then
    assert "Asia/Seoul" in repr(result.only)


@MYSQL_FAMILY_ONLY
def test_an_aware_value_compiles_identically_after_the_timezone_settings_changed(readonly_db):
    """Detects a wrapper kept across a ``USE_TZ`` change on MySQL / MariaDB only.

    Only their ``adapt_datetimefield_value`` reads the connection time zone; PostgreSQL passes an
    aware value through untouched, so this case would pass there even if the wrapper was never
    rebuilt. On PostgreSQL the stale wrapper is caught by
    ``test_the_timezone_name_is_not_kept_from_an_overridden_setting`` below (and the unit tests of
    ``wrapper_for``).
    """
    # when
    result = compare_aware_lookup_after_naive_override(aware_datetime_filter)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


@MYSQL_FAMILY_ONLY
def test_an_aware_value_is_adapted_in_utc_after_the_timezone_settings_changed(readonly_db):
    # when
    result = compare_aware_lookup_after_naive_override(aware_datetime_filter)
    # then
    assert result.only[2] == ("2024-01-02 03:04:05",)


def test_the_sql_still_matches_the_real_wrapper_once_the_timezone_settings_are_restored(
    readonly_db,
):
    """A regression guard for the override/restore sequence, not a stale-wrapper detector.

    Django's ``__date`` SQL reads ``get_current_timezone_name()``, not the wrapper's cached zone, so
    this passes even with a stale wrapper; ``test_the_timezone_name_is_not_kept_from_an_overridden_setting``
    is the one that notices it.
    """
    # when
    result = compare_after_timezone_override(published_date_lookup)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


def test_the_timezone_name_is_not_kept_from_an_overridden_setting(readonly_db):
    """The stale-wrapper detector on every backend (``USE_TZ`` False, only ``TIME_ZONE`` differs)."""
    # when
    names = timezone_names_after_naive_override()
    # then
    assert names[0] == names[1]


@MYSQL_ONLY
def test_a_mysql_update_filtered_through_a_join_needs_a_connection_while_compiling(readonly_db):
    # when
    result = compare_compilation(update_filtered_through_a_join)
    # then
    assert (result.real[:1], result.only) == (("sql",), ("raises", "CompileOnlyConnectionError"))


@SELF_SELECT_UPDATE_ONLY
def test_an_update_filtered_through_a_join_compiles_where_the_server_can_self_select(readonly_db):
    # when
    result = compare_compilation(update_filtered_through_a_join)
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")
