"""`wrapper_for` registry behaviour with unusual OPTIONS values (ssl contexts, locks, objects)."""

from __future__ import annotations

import ssl
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asyncopation.compile.postgresql import wrapper_for
from tests.support.compile_wrappers import PG_SETTINGS, pg_info


@dataclass(frozen=True)
class Leaves:
    """Option values that can be neither deep-copied nor compared by value."""

    ssl_context: ssl.SSLContext
    lock: Any
    adapters: object


def make_leaves() -> Leaves:
    return Leaves(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), threading.Lock(), object())


def settings_with(leaves: Leaves) -> dict[str, Any]:
    return {
        **PG_SETTINGS,
        "OPTIONS": {
            "ssl": leaves.ssl_context,
            "lock": leaves.lock,
            "adapters": leaves.adapters,
            "nested": {"hosts": ["a", "b"], "flags": {"x": 1}},
        },
    }


def _mutate_nested_dict(settings: dict[str, Any]) -> None:
    settings["OPTIONS"]["nested"]["flags"]["y"] = 2


def _mutate_nested_list(settings: dict[str, Any]) -> None:
    settings["OPTIONS"]["nested"]["hosts"].append("c")


def _swap_lock(settings: dict[str, Any]) -> None:
    settings["OPTIONS"]["lock"] = threading.Lock()


def _swap_adapters(settings: dict[str, Any]) -> None:
    settings["OPTIONS"]["adapters"] = object()


def _swap_ssl_context(settings: dict[str, Any]) -> None:
    settings["OPTIONS"]["ssl"] = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "nested-dict": _mutate_nested_dict,
    "nested-list": _mutate_nested_list,
    "lock": _swap_lock,
    "adapters": _swap_adapters,
    "ssl-context": _swap_ssl_context,
}


def reuse_with_same_dict(alias: str) -> bool:
    settings = settings_with(make_leaves())
    return wrapper_for(alias, settings, pg_info()) is wrapper_for(alias, settings, pg_info())


def reuse_with_fresh_dict(alias: str) -> bool:
    leaves = make_leaves()
    first = wrapper_for(alias, settings_with(leaves), pg_info())
    return first is wrapper_for(alias, settings_with(leaves), pg_info())


def reused_after_mutation(alias: str, mutation: str) -> bool:
    """Whether the same wrapper is returned after mutating the caller's dict in place."""
    settings = settings_with(make_leaves())
    first = wrapper_for(alias, settings, pg_info())
    MUTATIONS[mutation](settings)
    return first is wrapper_for(alias, settings, pg_info())


def reuse_with_ordered_dicts(alias: str) -> bool:
    """Dict subclasses (top level and nested) still compare equal and reuse the wrapper."""
    leaves = make_leaves()

    def build() -> dict[str, Any]:
        settings = OrderedDict(settings_with(leaves))
        settings["OPTIONS"] = OrderedDict(settings["OPTIONS"])
        return settings

    return wrapper_for(alias, build(), pg_info()) is wrapper_for(alias, build(), pg_info())


def reused_after_ordered_dict_mutation(alias: str) -> bool:
    """An OrderedDict OPTIONS mutated in place (same top-level object) yields a fresh wrapper."""
    settings = OrderedDict(settings_with(make_leaves()))
    settings["OPTIONS"] = OrderedDict(settings["OPTIONS"])
    first = wrapper_for(alias, settings, pg_info())
    settings["OPTIONS"]["extra"] = 1
    return first is wrapper_for(alias, settings, pg_info())
