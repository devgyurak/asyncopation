"""`CompileOperations.compose_sql`: plain-Python literal rendering, no connection (ADR-0008)."""

import pytest

from tests.support.compose_cases import COMPOSE_CASES, REJECTED_CASES, Money, Number
from tests.support.compose_sql import (
    compose_compile_only,
    compose_error,
    rejection_note,
)


@pytest.mark.parametrize(("sql", "params", "expected"), COMPOSE_CASES)
def test_compose_sql_renders_the_supported_literals_exactly(alias, sql, params, expected):
    # when
    composed = compose_compile_only(alias, sql, params)
    # then
    assert composed == expected


@pytest.mark.parametrize(("sql", "params", "error", "fragment"), REJECTED_CASES)
def test_compose_sql_rejects_what_it_cannot_render_exactly(alias, sql, params, error, fragment):
    # when
    outcome = compose_error(alias, sql, params, fragment)
    # then
    assert outcome == (error, True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param([1], "no note", id="list"),
        pytest.param((1,), "no note", id="tuple"),
        pytest.param(b"x", "no note", id="bytes"),
        pytest.param(Number.THREE, "exact-type note", id="int-enum"),
        pytest.param(Money("1"), "exact-type note", id="decimal-subclass"),
        pytest.param(5, "not rejected", id="an-exact-int-is-not-rejected"),
    ],
)
def test_only_subclasses_get_the_exact_type_note_in_their_rejection(alias, value, expected):
    # when
    outcome = rejection_note(alias, value)
    # then
    assert outcome == expected
