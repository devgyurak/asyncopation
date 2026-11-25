"""A SearchHeadline with options compiles without any connection (ADR-0008, compose_sql)."""

from tests.support.search_queries import compile_headline_in_scope


def test_search_headline_options_compile_inside_a_scope_without_connecting(alias):
    # when
    attempts, params = compile_headline_in_scope(alias, {"max_words": 5})
    # then
    assert (attempts, "MaxWords=5" in params) == (0, True)
