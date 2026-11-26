"""The sans-I/O cores give Django's answer on a sync runner and on the async runner (ADR-0007)."""

import pytest

from tests.support.backends import POSTGRESQL_ONLY, XFAIL_UNTIL_ASYNCMY
from tests.support.core_cases import (
    AGGREGATES,
    ANNOTATION_AGGREGATES,
    COMPOSITE_PK_AGGREGATES,
    INVALID_ALIAS_AGGREGATES,
    POSTGRESQL_ANNOTATION_AGGREGATES,
    POSTGRESQL_QUERIES,
    QUERIES,
    QUERIES_FOR_AGGREGATES,
    ZOO_AGGREGATES,
)
from tests.support.core_trio import (
    annotation_aggregation_trio,
    composite_pk_aggregation_trio,
    invalid_alias_aggregation_trio,
    named_aggregation_trio,
    named_count_trio,
    named_exists_trio,
    postgresql_annotation_aggregation_trio,
    postgresql_count_trio,
    postgresql_exists_trio,
    zoo_aggregation_trio,
)

pytestmark = XFAIL_UNTIL_ASYNCMY


@pytest.mark.parametrize("name", sorted(QUERIES))
async def test_count_core_matches_django_get_count_on_both_runners(async_env, name):
    # when
    trio = await named_count_trio(name)
    # then
    assert trio.matches_django


async def test_count_core_counts_a_seeded_table(async_env):
    # when
    trio = await named_count_trio("all")
    # then
    assert trio.async_runner == ("int", 100)


@pytest.mark.parametrize("name", ["empty-in", "empty-none", "no-match"])
async def test_count_core_of_nothing_is_zero_on_the_async_runner(async_env, name):
    # when
    trio = await named_count_trio(name)
    # then
    assert trio.async_runner == ("int", 0)


@pytest.mark.parametrize("name", ["empty-in", "empty-none", "no-match"])
async def test_count_core_of_nothing_matches_django_on_both_runners(async_env, name):
    # when
    trio = await named_count_trio(name)
    # then
    assert trio.matches_django


@pytest.mark.parametrize("name", sorted(QUERIES))
async def test_exists_core_matches_django_has_results_on_both_runners(async_env, name):
    # when
    trio = await named_exists_trio(name)
    # then
    assert trio.matches_django


@pytest.mark.parametrize(
    ("name", "expected"),
    [("all", True), ("empty-in", False), ("no-match", False), ("union", True)],
)
async def test_exists_core_answers_a_bool(async_env, name, expected):
    # when
    trio = await named_exists_trio(name)
    # then
    assert trio.async_runner == ("bool", expected)


@pytest.mark.parametrize("aggregate", sorted(AGGREGATES))
@pytest.mark.parametrize("query", sorted(QUERIES_FOR_AGGREGATES))
async def test_aggregation_core_matches_django_get_aggregation_on_both_runners(
    async_env, query, aggregate
):
    # when
    trio = await named_aggregation_trio(query, aggregate)
    # then
    assert trio.matches_django


@pytest.mark.parametrize("aggregate", sorted(ZOO_AGGREGATES))
async def test_aggregation_core_converts_driver_types_like_django(async_env, aggregate):
    # when
    trio = await zoo_aggregation_trio(aggregate)
    # then
    assert trio.matches_django


@pytest.mark.parametrize(
    ("aggregate", "type_name"),
    [("sum-decimal", "Decimal"), ("max-datetime", "datetime"), ("avg-decimal", "Decimal")],
)
async def test_aggregation_core_returns_the_python_type_of_the_field(
    async_env, aggregate, type_name
):
    # when
    trio = await named_aggregation_trio("all", aggregate)
    # then
    assert trio.async_type_name == type_name


async def test_aggregation_core_of_an_empty_set_returns_none_values(async_env):
    # when
    trio = await named_aggregation_trio("empty-in", "sum-decimal")
    # then
    assert trio.async_runner == ("dict", (("total", ("NoneType", None)),))


@pytest.mark.parametrize("name", sorted(ANNOTATION_AGGREGATES))
async def test_aggregation_core_over_annotations_matches_django_on_both_runners(async_env, name):
    # when
    trio = await annotation_aggregation_trio(name)
    # then
    assert trio.matches_django


@POSTGRESQL_ONLY
@pytest.mark.parametrize("name", sorted(POSTGRESQL_ANNOTATION_AGGREGATES))
async def test_aggregation_core_over_a_set_returning_annotation_matches_django(async_env, name):
    # when
    trio = await postgresql_annotation_aggregation_trio(name)
    # then
    assert trio.matches_django


@POSTGRESQL_ONLY
@pytest.mark.parametrize("name", sorted(POSTGRESQL_QUERIES))
async def test_count_core_of_a_set_returning_annotation_matches_django(async_env, name):
    # when
    trio = await postgresql_count_trio(name)
    # then
    assert trio.matches_django


@POSTGRESQL_ONLY
@pytest.mark.parametrize("name", sorted(POSTGRESQL_QUERIES))
async def test_exists_core_of_a_set_returning_annotation_matches_django(async_env, name):
    # when
    trio = await postgresql_exists_trio(name)
    # then
    assert trio.matches_django


@pytest.mark.parametrize("name", sorted(COMPOSITE_PK_AGGREGATES))
async def test_aggregation_core_over_the_composite_pk_matches_django(async_env, name):
    # when
    trio = await composite_pk_aggregation_trio(name)
    # then
    assert trio.agrees_with_django


@pytest.mark.parametrize("name", sorted(INVALID_ALIAS_AGGREGATES))
async def test_aggregation_core_rejects_an_invalid_alias_with_a_value_error(async_env, name):
    # when
    trio = await invalid_alias_aggregation_trio(name)
    # then
    assert trio.async_runner[:2] == ("error", "ValueError")


@pytest.mark.parametrize("name", sorted(INVALID_ALIAS_AGGREGATES))
async def test_aggregation_core_gives_django_error_for_an_invalid_alias_on_both_runners(
    async_env, name
):
    # when
    trio = await invalid_alias_aggregation_trio(name)
    # then
    assert trio.runners_match_django
