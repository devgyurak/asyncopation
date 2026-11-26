"""`compile.mysql.wrapper_for` registry scenarios with unusual OPTIONS values."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from asyncopation.compile.mysql import wrapper_for
from tests.support.mysql_wrappers import MYSQL_SETTINGS, mysql_info
from tests.support.wrapper_registry import MUTATIONS, make_leaves, settings_with


def mysql_settings_with(leaves: Any) -> dict[str, Any]:
    return {**settings_with(leaves), **{k: v for k, v in MYSQL_SETTINGS.items() if k != "OPTIONS"}}


def reuse_with_same_dict(alias: str) -> bool:
    settings = mysql_settings_with(make_leaves())
    return wrapper_for(alias, settings, mysql_info()) is wrapper_for(alias, settings, mysql_info())


def reuse_with_fresh_dict(alias: str) -> bool:
    leaves = make_leaves()
    first = wrapper_for(alias, mysql_settings_with(leaves), mysql_info())
    return first is wrapper_for(alias, mysql_settings_with(leaves), mysql_info())


def reused_after_mutation(alias: str, mutation: str) -> bool:
    settings = mysql_settings_with(make_leaves())
    first = wrapper_for(alias, settings, mysql_info())
    MUTATIONS[mutation](settings)
    return first is wrapper_for(alias, settings, mysql_info())


def reuse_with_ordered_dicts(alias: str) -> bool:
    leaves = make_leaves()

    def build() -> dict[str, Any]:
        settings = OrderedDict(mysql_settings_with(leaves))
        settings["OPTIONS"] = OrderedDict(settings["OPTIONS"])
        return settings

    return wrapper_for(alias, build(), mysql_info()) is wrapper_for(alias, build(), mysql_info())


def reused_after_ordered_dict_mutation(alias: str) -> bool:
    settings = OrderedDict(mysql_settings_with(make_leaves()))
    settings["OPTIONS"] = OrderedDict(settings["OPTIONS"])
    first = wrapper_for(alias, settings, mysql_info())
    settings["OPTIONS"]["extra"] = 1
    return first is wrapper_for(alias, settings, mysql_info())


def feature_across_info_change(alias: str, feature: str, version: str) -> tuple[Any, Any]:
    """`feature` of the wrapper built for the default server, and of the one that replaces it when
    the server reports `version` (the first one has read the feature before it is replaced)."""
    first = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    stale = getattr(first.features, feature)
    second = wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info(version))
    return stale, getattr(second.features, feature)


def registered_after_clear(alias: str) -> tuple[bool, bool]:
    """Whether the MySQL and the PostgreSQL registry still hold a wrapper of `alias` after
    ``clear_alias`` (both held one before)."""
    from asyncopation.compile import postgresql
    from tests.support.compile_wrappers import PG_SETTINGS, clear_alias, pg_info

    wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info())
    postgresql.wrapper_for(alias, dict(PG_SETTINGS), pg_info())
    clear_alias(alias)
    return alias in mysql_registry(), alias in postgresql._WRAPPERS


def mysql_registry() -> dict[str, Any]:
    from asyncopation.compile import mysql

    return mysql._WRAPPERS
