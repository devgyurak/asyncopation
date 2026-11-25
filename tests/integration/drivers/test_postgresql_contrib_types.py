"""hstore/citext registration on the async driver (#43, ADR-0003 Decision 2).

With ``django.contrib.postgres`` installed Django registers hstore and citext[] handlers on every
new connection; the async driver must hand back the same Python values and types.
"""

import pytest

from tests.support.drivers import POSTGRES_ONLY, compare_with_sync
from tests.support.drivers.contrib import (
    async_column,
    compare_zoo_with_sync,
    compare_zoo_without_contrib,
    concurrent_hstore_reads,
    configure_unopened_driver,
    element_types,
    hstore_in_transaction_and_after_rollback,
    insert_and_read_back,
    loader_presence,
    open_and_query_in_database_without_extensions,
    pg_field_zoo_sql,
    python_types,
    read_from_each_schema,
    read_hstore_as_assumed_role,
)

pytestmark = POSTGRES_ONLY

UNICODE_HSTORE = {"한글": "값 ✓", 'q"uote': "c,o=>m\\a", "": ""}
COLUMN_VALUES = [
    pytest.param(
        "hstore",
        [
            {},
            {"a": "1", "b": None},
            UNICODE_HSTORE,
            {"NULL": "null", "space key": " padded "},
        ],
        id="hstore",
    ),
    pytest.param("nullable_hstore", [None, None, {}, {"only": None}], id="nullable-hstore"),
    pytest.param(
        "hstore_list",
        [[], [{"k": "v"}, {}], [{"x": None}], []],
        id="hstore-array",
    ),
    pytest.param("citext", ["", "MiXeD", "", "ÄÖÜ"], id="citext"),
    pytest.param(
        "citext_list",
        [[], ["Alpha", "beta"], [], ["a,b", '"q"']],
        id="citext-array",
    ),
]


# --------------------------------------------------------------------------- reads
async def test_pg_field_zoo_rows_match_sync_cursor(readonly_db, binding):
    # when
    result = await compare_zoo_with_sync(binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.sync_rows) == 4


@pytest.mark.parametrize(("column", "expected"), COLUMN_VALUES)
async def test_column_reads_as_python_values(readonly_db, binding, column, expected):
    # when
    values = await async_column(column, binding.options)
    # then
    assert values == expected


async def test_hstore_values_are_dicts_not_text(readonly_db, binding):
    # when
    values = await async_column("hstore", binding.options)
    # then
    assert python_types(values) == {dict}


async def test_citext_array_values_are_lists_of_str(readonly_db, binding):
    # when
    values = await async_column("citext_list", binding.options)
    # then
    assert python_types(values) == {list}
    assert element_types(values) == {str}


async def test_scalar_citext_values_are_str(readonly_db, binding):
    # when
    values = await async_column("citext", binding.options)
    # then
    assert python_types(values) == {str}


# --------------------------------------------------------------------------- parameters (dump)
async def test_dict_parameter_round_trips_like_sync_cursor(readonly_db, binding):
    # given
    sql = "SELECT %s::hstore"
    # when
    result = await compare_with_sync(
        sql, ({"a": "1", "b": None, "": "x"},), options=binding.options, register_contrib_types=True
    )
    # then
    assert result.async_rows == result.sync_rows
    assert result.async_rows[0][0][0] == "dict"


async def test_dict_parameter_filters_an_hstore_column_like_sync_cursor(readonly_db, binding):
    # given
    sql = pg_field_zoo_sql("id").replace("ORDER BY id", "WHERE hstore = %s ORDER BY id")
    # when
    result = await compare_with_sync(
        sql, ({"a": "1", "b": None},), options=binding.options, register_contrib_types=True
    )
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.async_rows) == 1


async def test_dict_parameter_inserted_through_the_driver_reads_back_equal(hstore_table, binding):
    # given
    value = {"k": "v", "n": None, 'q"uote': "c,o=>m\\a", "한글": "✓"}
    # when
    stored = await insert_and_read_back(hstore_table, "data", value, binding.options)
    # then
    assert stored == [value]


async def test_list_of_dicts_inserted_through_the_driver_reads_back_equal(hstore_table, binding):
    # given
    value = [{"a": "1"}, {}, {"b": None}]
    # when
    stored = await insert_and_read_back(hstore_table, "tags", value, binding.options)
    # then
    assert stored == [value]


# --------------------------------------------------------------------------- flag off
async def test_hstore_reads_as_text_without_contrib_postgres_like_sync_django(readonly_db, binding):
    # when
    result = await compare_zoo_without_contrib(binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.sync_rows) == 4


async def test_hstore_is_str_when_the_flag_is_off(readonly_db, binding):
    # when
    result = await compare_zoo_without_contrib(binding.options)
    # then
    assert result.async_rows[1][0] == ("str", '\'"a"=>"1", "b"=>NULL\'')


# --------------------------------------------------------------------------- no extensions
async def test_open_succeeds_in_a_database_without_the_extensions(bare_database):
    # when
    outcome = await open_and_query_in_database_without_extensions(bare_database)
    # then
    assert outcome.rows == [(1, "x")]


async def test_open_still_asks_for_the_oids_in_a_database_without_the_extensions(bare_database):
    # when
    outcome = await open_and_query_in_database_without_extensions(bare_database)
    # then
    assert sorted(outcome.catalog_queries) == ["citext", "hstore"]


# --------------------------------------------------------------------------- configure step
async def test_configure_without_fetched_oids_raises_runtime_error_when_the_flag_is_on(
    django_db_setup,
):
    # given: open() always fetches the OIDs first, so only this direct call reaches the invariant
    # when / then
    with pytest.raises(RuntimeError, match="OIDs are unknown"):
        await configure_unopened_driver(register=True)


async def test_configure_without_fetched_oids_is_fine_when_the_flag_is_off(django_db_setup):
    # when
    has_hstore_loader = await configure_unopened_driver(register=False)
    # then
    assert has_hstore_loader is False


# --------------------------------------------------------------------------- loaders
async def test_every_physical_connection_has_the_hstore_loaders(readonly_db):
    # when
    presence = await loader_presence()
    # then
    assert presence.connections_without_hstore == 0
    assert presence.connections_seen >= 3  # the probe and two pool connections: not vacuous


async def test_the_shared_adapters_template_gets_no_hstore_loader(readonly_db):
    # when
    presence = await loader_presence()
    # then
    assert presence.template_lacks_hstore


# --------------------------------------------------------------------------- more shapes
async def test_citext_array_parameter_round_trips_like_sync_cursor(readonly_db, binding):
    # when
    result = await compare_with_sync(
        "SELECT %s::citext[]",
        (["Alpha", "beta", "a,b"],),
        options=binding.options,
        register_contrib_types=True,
    )
    # then
    assert result.async_rows == result.sync_rows
    assert result.async_rows[0][0][0] == "list"


async def test_hstore_reads_as_dict_from_every_pool_connection_at_once(readonly_db):
    # when
    values = await concurrent_hstore_reads(4)
    # then
    assert values == [{"a": "1"}] * 4


async def test_hstore_reads_as_dict_inside_a_transaction_and_after_rollback(readonly_db, binding):
    # when
    values = await hstore_in_transaction_and_after_rollback()
    # then
    assert values == [{"a": "1"}, {"a": "1"}]


async def test_hstore_reads_as_dict_with_assume_role(readonly_db, binding):
    # when
    outcome = await read_hstore_as_assumed_role(binding.options)
    # then
    assert outcome.value == {"a": "1"}


async def test_assume_role_still_costs_two_catalog_queries(readonly_db, binding):
    # when
    outcome = await read_hstore_as_assumed_role(binding.options)
    # then
    assert sorted(outcome.catalog_queries) == ["citext", "hstore"]


async def test_hstore_types_in_several_schemas_are_all_registered(multi_schema_database):
    # when
    values = await read_from_each_schema(multi_schema_database)
    # then
    assert values == [{"a": "1"}, {"b": None}]
