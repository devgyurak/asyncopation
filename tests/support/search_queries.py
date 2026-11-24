"""contrib.postgres SearchHeadline queries (they call `compose_sql` while compiling)."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.postgres.search import SearchHeadline, SearchQuery  # ty: ignore[missing-module-member]

from harness.catalog.models import Book

# name -> (SearchHeadline options, the option string Django composes for them, copied from a live run)
HEADLINE_CASES: dict[str, tuple[dict[str, Any], str]] = {
    "max-words": ({"max_words": 5}, "MaxWords=5"),
    "min-words": ({"min_words": 2}, "MinWords=2"),
    "short-word": ({"short_word": 2}, "ShortWord=2"),
    "highlight-all": ({"highlight_all": True}, "HighlightAll=true"),
    "max-fragments": ({"max_fragments": 3}, "MaxFragments=3"),
    "fragment-delimiter": ({"fragment_delimiter": " ... "}, "FragmentDelimiter=' ... '"),
    "selectors": ({"start_sel": "<b>", "stop_sel": "</b>"}, "StartSel='<b>', StopSel='</b>'"),
    "selectors-with-quotes": (
        {"start_sel": "<b class='x'>", "stop_sel": '</b "y">'},
        "StartSel='<b class=''x''>', StopSel='</b \"y\">'",
    ),
    "selectors-with-specials": (
        {"start_sel": "[%]\\", "stop_sel": "\u2713;--"},
        "StartSel= E'[%]\\\\', StopSel='\u2713;--'",
    ),
    "float-selectors": ({"start_sel": 1.5, "stop_sel": -2.5}, "StartSel=1.5, StopSel= -2.5"),
    "float-delimiter": ({"fragment_delimiter": 0.5}, "FragmentDelimiter=0.5"),
    "everything": (
        {
            "max_words": 20,
            "min_words": 5,
            "short_word": 3,
            "highlight_all": False,
            "max_fragments": 2,
            "fragment_delimiter": "~",
            "start_sel": "<i>",
            "stop_sel": "</i>",
        },
        "StartSel='<i>', StopSel='</i>', MaxWords=20, MinWords=5, ShortWord=3, "
        "HighlightAll=false, MaxFragments=2, FragmentDelimiter='~'",
    ),
}


HEADLINE_OPTION_PARAMS = [pytest.param(o, id=k) for k, (o, _) in HEADLINE_CASES.items()]
HEADLINE_COMPOSED_PARAMS = [pytest.param(o, c, id=k) for k, (o, c) in HEADLINE_CASES.items()]


def headline_query(options: dict[str, Any]) -> Any:
    headline = SearchHeadline("title", SearchQuery("x"), **options)
    return Book.objects.annotate(headline=headline).query


def compile_headline_in_scope(alias: str, options: dict[str, Any]) -> tuple[int, tuple[Any, ...]]:
    """Compile a headline query on a fresh compile-only wrapper: (connection attempts, params)."""
    from asyncopation.compile.scope import compile_scope
    from tests.support.compile_wrappers import make_wrapper
    from tests.support.connect_counter import count_sync_connection_attempts

    query = headline_query(options)
    wrapper = make_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        compile_scope(alias, wrapper, autocommit=True),
    ):
        _, params = query.get_compiler(using=alias).as_sql()
    return counter.count, tuple(params)
