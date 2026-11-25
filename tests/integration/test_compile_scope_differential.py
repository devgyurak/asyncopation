"""Compile-only wrapper inside compile_scope generates the same SQL as Django's real wrapper.

TODO(#6): materialization outside the scope is verified when the executor lands.
"""

from functools import partial

import pytest
from hypothesis import given

from harness.settings import VENDOR
from tests.support.compile_differential import (
    annotated_books,
    compare_compilation,
    distinct_books,
    exists_of,
    filtered_books,
    for_update_of_query,
    for_update_query,
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

pytestmark = pytest.mark.skipif(VENDOR != "postgresql", reason="compile-only wrapper: PostgreSQL")

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
    "build", [for_update_query, for_update_of_query], ids=["plain", "of-nowait"]
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


@pytest.mark.parametrize("options", HEADLINE_OPTION_PARAMS)
def test_search_headline_options_compile_identically(readonly_db, options):
    # when
    result = compare_compilation(partial(headline_query, options))
    # then
    assert (result.verdict, result.only[0]) == (result.expected, "sql")


@pytest.mark.parametrize(("options", "composed"), HEADLINE_COMPOSED_PARAMS)
def test_search_headline_options_are_composed_into_the_parameters(readonly_db, options, composed):
    # when
    result = compare_compilation(partial(headline_query, options))
    # then
    assert composed in result.only[2]
