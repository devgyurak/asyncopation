"""Server data is injected into the MySQL compile-only wrapper: no connection is ever needed."""

import pytest

from tests.support.mysql_wrappers import (
    HANDSHAKE_VERSION,
    VERSIONS,
    check_constraint_names,
    expected_explain,
    expected_regex,
    explain_prefix_of,
    fact_of,
    fact_params,
    feature_before_and_after,
    fresh_wrapper,
    injected_flags,
    integer_flags_info,
    mysql_info,
    ops_call,
    regex_of,
    scenario_info,
    version_facts,
)

SCENARIOS = pytest.mark.parametrize("scenario", sorted(VERSIONS))


@pytest.mark.parametrize(("scenario", "path", "expected"), fact_params())
def test_a_server_release_determines_the_derived_facts_before_any_connection(
    scenario, path, expected
):
    # given
    wrapper = fresh_wrapper(scenario_info(scenario))
    # when
    actual = fact_of(wrapper, path)
    # then
    assert actual == expected


@SCENARIOS
def test_the_explain_prefix_follows_the_server_flavour(scenario):
    # given
    wrapper = fresh_wrapper(scenario_info(scenario))
    # when
    prefix = explain_prefix_of(wrapper)
    # then
    assert prefix == expected_explain(scenario)


@SCENARIOS
def test_the_regex_lookup_follows_the_server_flavour(scenario):
    # given
    wrapper = fresh_wrapper(scenario_info(scenario))
    # when
    sql = regex_of(wrapper)
    # then
    assert sql == expected_regex(scenario)


@SCENARIOS
def test_for_update_sql_is_the_same_on_every_release(scenario):
    # given
    wrapper = fresh_wrapper(scenario_info(scenario))
    # when
    sql = ops_call(wrapper, "for_update_sql", skip_locked=True)
    # then
    assert sql == "FOR UPDATE SKIP LOCKED"


@SCENARIOS
def test_the_check_constraints_cover_the_positive_integer_fields(scenario):
    # given
    wrapper = fresh_wrapper(scenario_info(scenario))
    # when
    names = check_constraint_names(wrapper)
    # then
    assert names == [
        "PositiveBigIntegerField",
        "PositiveIntegerField",
        "PositiveSmallIntegerField",
    ]


def test_the_sql_mode_is_a_set_of_the_comma_separated_modes():
    # given
    wrapper = fresh_wrapper(mysql_info(sql_mode="STRICT_TRANS_TABLES,NO_ZERO_DATE"))
    # when
    mode = wrapper.sql_mode
    # then
    assert mode == {"STRICT_TRANS_TABLES", "NO_ZERO_DATE"}


def test_an_empty_sql_mode_is_the_empty_set():
    # given
    wrapper = fresh_wrapper(mysql_info(sql_mode=""))
    # when
    mode = wrapper.sql_mode
    # then
    assert mode == set()


def test_the_server_version_string_is_exposed_unchanged():
    # given
    wrapper = fresh_wrapper(mysql_info("11.8.1-MariaDB-ubu2404"))
    # when
    info = wrapper.mysql_server_info
    # then
    assert info == "11.8.1-MariaDB-ubu2404"


@pytest.mark.parametrize(
    ("version", "expected"),
    [("8.4.3", False), ("5.7.44-log", False), ("10.11.6-MariaDB", True), ("11.8.1-mariadb", True)],
)
def test_mariadb_is_recognised_case_insensitively_in_the_version_string(version, expected):
    # given
    wrapper = fresh_wrapper(mysql_info(version))
    # when
    mariadb = wrapper.mysql_is_mariadb
    # then
    assert mariadb is expected


@pytest.mark.parametrize("version", ["garbage", "", "8.4", "x.y.z"])
def test_an_unparsable_version_string_raises_like_django(version):
    # given
    wrapper = fresh_wrapper(mysql_info(version))
    # when / then
    with pytest.raises(Exception, match="Unable to determine MySQL version"):
        wrapper.mysql_version  # noqa: B018


def test_a_myisam_default_engine_turns_transactions_off():
    # given
    wrapper = fresh_wrapper(mysql_info(engine="MyISAM"))
    # when
    supported = wrapper.features.supports_transactions
    # then
    assert supported is False


@pytest.mark.parametrize(
    ("change", "feature", "default", "changed"),
    [
        ({"has_zoneinfo_database": False}, "has_zoneinfo_database", True, False),
        ({"sql_auto_is_null": True}, "is_sql_auto_is_null_enabled", False, True),
        ({"lower_case_table_names": True}, "ignores_table_name_case", False, True),
    ],
    ids=["zoneinfo", "auto_is_null", "lower_case"],
)
def test_non_default_server_flags_reach_the_wrapper(change, feature, default, changed):
    # when
    observed = feature_before_and_after(feature, change)
    # then
    assert observed == (default, changed)


def test_the_flags_are_coerced_to_bool_like_django_does_with_the_row():
    # when
    flags = injected_flags(integer_flags_info())
    # then
    assert flags == ((True, bool), (True, bool), (False, bool))


def test_a_mariadb_handshake_version_is_not_what_version_returns():
    # when
    facts = version_facts(HANDSHAKE_VERSION)
    # then: the replication-hack prefix wins, as in Django, so the #7 probe must pass VERSION()
    assert facts == ((5, 5, 5), True)


def test_the_version_a_mariadb_server_reports_for_version_is_parsed_from_its_own_number():
    # when
    facts = version_facts("10.11.6-MariaDB")
    # then
    assert facts == ((10, 11, 6), True)
