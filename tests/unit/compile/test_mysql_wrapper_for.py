"""MySQL `wrapper_for`: one shared compile-only wrapper per alias, replaced when inputs change."""

import pytest

from asyncopation.compile.mysql import wrapper_for
from asyncopation.compile.postgresql import wrapper_for as pg_wrapper_for
from asyncopation.compile.server_info import PostgreSQLServerInfo
from tests.support.compile_wrappers import PG_SETTINGS, pg_info
from tests.support.mysql_registry import (
    feature_across_info_change,
    registered_after_clear,
    reuse_with_fresh_dict,
    reuse_with_ordered_dicts,
    reuse_with_same_dict,
    reused_after_mutation,
    reused_after_ordered_dict_mutation,
)
from tests.support.mysql_wrappers import MYSQL_SETTINGS, make_mysql_wrapper, mutate_info, mysql_info
from tests.support.wrapper_registry import MUTATIONS
from tests.support.wrapper_timezone import (
    timezone_name_during,
    wrappers_across,
    wrappers_across_unchanged,
)


def test_wrapper_for_shares_one_wrapper_per_alias(alias):
    # when
    first = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    second = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    # then
    assert first is second


def test_wrapper_for_replaces_the_wrapper_when_the_info_changes(alias):
    # given
    first = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info("8.4.3"))
    # when
    second = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info("11.8.1-MariaDB"))
    # then
    assert first is not second


def test_wrapper_for_serves_the_new_version_after_the_info_changes(alias):
    # given
    wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info("8.4.3"))
    # when
    second = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info("11.8.1-MariaDB"))
    # then
    assert second.mysql_version == (11, 8, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"sql_mode": ""},
        {"default_storage_engine": "MyISAM"},
        {"sql_auto_is_null": True},
        {"lower_case_table_names": True},
        {"has_zoneinfo_database": False},
    ],
    ids=["sql_mode", "engine", "auto_is_null", "lower_case", "zoneinfo"],
)
def test_wrapper_for_replaces_the_wrapper_when_any_server_fact_changes(alias, change):
    # given
    first = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    # when
    second = wrapper_for(alias, dict(MYSQL_SETTINGS), mutate_info(mysql_info(), **change))
    # then
    assert first is not second


def test_wrapper_for_does_not_serve_stale_features_after_the_info_changes(alias):
    # when
    served = feature_across_info_change(alias, "has_select_for_update_of", "11.8.1-MariaDB")
    # then
    assert served == (True, False)


def test_wrapper_for_keeps_a_copy_of_the_settings(alias):
    # given
    settings = dict(MYSQL_SETTINGS)
    first = wrapper_for(alias, settings, mysql_info())
    settings["NAME"] = "mutated"
    # when
    second = wrapper_for(alias, settings, mysql_info())
    # then
    assert first is not second


def test_the_wrapper_settings_are_not_changed_by_a_later_mutation_of_the_callers_dict(alias):
    # given
    settings = dict(MYSQL_SETTINGS)
    first = wrapper_for(alias, settings, mysql_info())
    # when
    settings["NAME"] = "mutated"
    # then
    assert first.settings_dict["NAME"] == "x"


def test_wrapper_for_replaces_the_wrapper_when_the_settings_change(alias):
    # given
    first = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    # when
    second = wrapper_for(alias, {**MYSQL_SETTINGS, "NAME": "other"}, mysql_info())
    # then
    assert first is not second


def test_the_replacement_wrapper_carries_the_new_settings(alias):
    # given
    wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    # when
    second = wrapper_for(alias, {**MYSQL_SETTINGS, "NAME": "other"}, mysql_info())
    # then
    assert second.settings_dict["NAME"] == "other"


def test_wrapper_for_separates_aliases(alias):
    # when
    first = make_mysql_wrapper(alias)
    second = make_mysql_wrapper(alias + "_other")
    # then
    assert first is not second


def test_the_server_info_is_kept_on_the_wrapper(alias):
    # given
    info = mysql_info("11.8.1-MariaDB")
    # when
    wrapper = wrapper_for(alias, dict(MYSQL_SETTINGS), info)
    # then
    assert wrapper.server_info == info


def test_wrapper_for_rejects_postgresql_server_info(alias):
    # when / then
    with pytest.raises(TypeError, match="MySQLServerInfo"):
        wrapper_for(alias, dict(MYSQL_SETTINGS), PostgreSQLServerInfo(pg_version=170000))  # ty: ignore[invalid-argument-type]


def test_the_postgresql_registry_rejects_mysql_server_info(alias):
    # when / then
    with pytest.raises(TypeError, match="PostgreSQLServerInfo"):
        pg_wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())  # ty: ignore[invalid-argument-type]


def test_the_postgresql_registry_keeps_its_wrapper_when_mysql_registers_the_same_alias(alias):
    # given
    pg = pg_wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    make_mysql_wrapper(alias)
    # when
    looked_up = pg_wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    # then
    assert looked_up is pg


def test_the_mysql_registry_keeps_its_wrapper_when_postgresql_registers_the_same_alias(alias):
    # given
    mysql = make_mysql_wrapper(alias)
    pg_wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    # when
    looked_up = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    # then
    assert looked_up is mysql


def test_applying_server_info_twice_raises(alias):
    # given
    wrapper = make_mysql_wrapper(alias)
    # when / then
    with pytest.raises(RuntimeError, match="once per wrapper"):
        wrapper.apply_server_info(mysql_info())


def test_unusual_option_values_do_not_break_wrapper_reuse_with_the_same_dict(alias):
    # when
    reused = reuse_with_same_dict(alias)
    # then
    assert reused is True


def test_a_fresh_dict_holding_the_same_leaf_objects_reuses_the_wrapper(alias):
    # when
    reused = reuse_with_fresh_dict(alias)
    # then
    assert reused is True


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_changing_the_callers_settings_yields_a_fresh_wrapper(alias, mutation):
    # when
    reused = reused_after_mutation(alias, mutation)
    # then
    assert reused is False


def test_dict_subclasses_still_compare_equal_and_reuse_the_wrapper(alias):
    # when
    reused = reuse_with_ordered_dicts(alias)
    # then
    assert reused is True


def test_mutating_an_ordered_dict_in_place_yields_a_fresh_wrapper(alias):
    # when
    reused = reused_after_ordered_dict_mutation(alias)
    # then
    assert reused is False


BACKENDS = pytest.mark.parametrize("backend", ["mysql", "postgresql"])


@BACKENDS
@pytest.mark.parametrize(
    "override",
    [{"USE_TZ": False}, {"TIME_ZONE": "Asia/Seoul"}],
    ids=["use_tz", "time_zone"],
)
def test_wrapper_for_replaces_the_wrapper_when_the_timezone_settings_change(
    alias, backend, override
):
    # when
    before, during, _ = wrappers_across(backend, alias, **override)
    # then
    assert before is not during


@BACKENDS
def test_wrapper_for_replaces_the_wrapper_once_more_when_the_overridden_settings_are_restored(
    alias, backend
):
    # when
    _, during, after = wrappers_across(backend, alias, TIME_ZONE="Asia/Seoul")
    # then
    assert during is not after


@BACKENDS
def test_the_replacement_wrapper_reports_the_overridden_timezone(alias, backend):
    # when
    name = timezone_name_during(backend, alias, "Asia/Seoul")
    # then
    assert name == "Asia/Seoul"


@BACKENDS
def test_wrapper_for_keeps_the_wrapper_while_the_timezone_settings_are_unchanged(alias, backend):
    # when
    first, second = wrappers_across_unchanged(backend, alias)
    # then
    assert first is second


def test_clearing_an_alias_drops_its_registered_wrappers_of_both_backends(alias):
    # when
    still_registered = registered_after_clear(alias)
    # then
    assert still_registered == (False, False)
