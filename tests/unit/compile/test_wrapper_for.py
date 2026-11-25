"""`wrapper_for`: one shared compile-only wrapper per alias, replaced when its inputs change."""

from asyncopation.compile.postgresql import wrapper_for
from tests.support.compile_wrappers import PG_SETTINGS, make_wrapper, pg_info


def test_wrapper_for_shares_one_wrapper_per_alias(alias):
    # when
    first = wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    second = wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    # then
    assert first is second


def test_wrapper_for_replaces_the_wrapper_when_the_info_changes(alias):
    # given
    first = wrapper_for(alias, dict(PG_SETTINGS), pg_info(150000))
    # when
    second = wrapper_for(alias, dict(PG_SETTINGS), pg_info(170000))
    # then
    assert (first is not second, second.pg_version) == (True, 170000)


def test_wrapper_for_does_not_serve_stale_features_after_the_info_changes(alias):
    # given
    first = wrapper_for(alias, dict(PG_SETTINGS), pg_info(150000))
    stale = first.features.is_postgresql_16  # ty: ignore[missing-attribute]
    # when
    second = wrapper_for(alias, dict(PG_SETTINGS), pg_info(170000))
    fresh = second.features.is_postgresql_16  # ty: ignore[missing-attribute]
    # then
    assert (stale, fresh) == (False, True)


def test_wrapper_for_keeps_a_copy_of_the_settings(alias):
    # given
    settings = dict(PG_SETTINGS)
    first = wrapper_for(alias, settings, pg_info())
    settings["NAME"] = "mutated"
    # when
    second = wrapper_for(alias, settings, pg_info())
    # then
    assert (first is not second, first.settings_dict["NAME"]) == (True, "x")


def test_wrapper_for_replaces_the_wrapper_when_the_settings_change(alias):
    # given
    first = wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    # when
    second = wrapper_for(alias, {**PG_SETTINGS, "NAME": "other"}, pg_info())
    # then
    assert (first is not second, second.settings_dict["NAME"]) == (True, "other")


def test_wrapper_for_separates_aliases(alias):
    # when
    first = make_wrapper(alias)
    second = make_wrapper(alias + "_other")
    # then
    assert first is not second
