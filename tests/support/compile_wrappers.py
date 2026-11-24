"""Compile-only wrapper builders and aliases for the ADR-0008 tests."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from asyncopation._django.postgresql import BaseWrapper
from asyncopation.compile.postgresql import PostgreSQLCompileWrapper, wrapper_for
from asyncopation.compile.server_info import PostgreSQLServerInfo
from tests.support import connection_entry as boundary

PG_VERSION = 170000
PG_SETTINGS: dict[str, Any] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "x",
    "OPTIONS": {},
    "TIME_ZONE": None,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
    "TEST": {},
}
_COUNTER = itertools.count()


def unique_alias() -> str:
    return f"asyncopation_unit_{next(_COUNTER)}"


def clear_alias(alias: str) -> None:
    if boundary.peek(alias) is not None:
        boundary.remove(alias)


def pg_info(version: int = PG_VERSION) -> PostgreSQLServerInfo:
    return PostgreSQLServerInfo(pg_version=version)


def make_wrapper(alias: str, version: int = PG_VERSION) -> PostgreSQLCompileWrapper:
    """The shared compile-only wrapper of `alias` (one per alias)."""
    return wrapper_for(alias, dict(PG_SETTINGS), pg_info(version))


def make_distinct_wrapper(alias: str) -> PostgreSQLCompileWrapper:
    """A wrapper that is not the shared one (to play the 'previous' wrapper)."""
    wrapper = PostgreSQLCompileWrapper(dict(PG_SETTINGS), alias)
    wrapper.apply_server_info(pg_info())
    return wrapper


def make_real_pg_wrapper(alias: str) -> BaseWrapper:
    """Django's own wrapper, constructed without I/O."""
    return BaseWrapper(dict(PG_SETTINGS), alias)


def wrapper_classes_that_can_connect(prefix: str = "asyncopation.") -> list[str]:
    """Classes (module starting with `prefix`) based on Django's real wrapper, directly or not,
    without the compile-only mixin before it in the MRO."""
    import asyncopation.compile.postgresql  # noqa: F401 - registers the subclass
    from asyncopation._django.postgresql import BaseWrapper
    from asyncopation.compile.wrapper import CompileOnlyMixin

    return sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in all_subclasses(BaseWrapper)
        if cls.__module__.startswith(prefix)
        and not _mixin_first(cls, CompileOnlyMixin, BaseWrapper)
    )


def all_subclasses(base: type) -> list[type]:
    found: dict[type, None] = {}
    pending = list(base.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls not in found:
            found[cls] = None
            pending.extend(cls.__subclasses__())
    return list(found)


def _mixin_first(cls: type, mixin: type, real: type) -> bool:
    mro = cls.__mro__
    return mixin in mro and mro.index(mixin) < mro.index(real)


def throwaway_wrapper_report() -> list[str]:
    """What the MRO check reports for a few deliberately built classes.

    The classes live in a module name unique to the call and stay registered as `BaseWrapper`
    subclasses for the rest of the process; the production check ignores them (it only looks at
    `asyncopation.` modules).
    """
    from asyncopation._django.postgresql import BaseWrapper
    from asyncopation.compile.wrapper import CompileOnlyMixin

    name = f"throwaway_mro_{uuid.uuid4().hex}"
    module = {"__module__": name}
    no_mixin = type("NoMixin", (BaseWrapper,), module)
    kept = [
        no_mixin,
        type("MixinLast", (BaseWrapper, CompileOnlyMixin), module),
        type("Good", (CompileOnlyMixin, BaseWrapper), module),
        type("Indirect", (no_mixin,), module),
        type("Elsewhere", (BaseWrapper,), {"__module__": "other_module"}),
    ]  # references keep the classes alive until the report is taken
    report = wrapper_classes_that_can_connect(name)
    assert kept
    return [entry.removeprefix(name + ".") for entry in report]


def lookalike_module_report() -> list[str]:
    """The default check on classes in modules that only look like `asyncopation`."""
    from asyncopation._django.postgresql import BaseWrapper

    kept = [
        type("Lookalike", (BaseWrapper,), {"__module__": "asyncopationx.mod"}),
        type("Other", (BaseWrapper,), {"__module__": "a.mod"}),
    ]
    report = wrapper_classes_that_can_connect()
    assert kept
    return report


def wrapper_classes_found(prefix: str = "asyncopation.") -> list[str]:
    """Classes (module starting with `prefix`) based on Django's real wrapper, however indirectly."""
    from asyncopation._django.postgresql import BaseWrapper

    return sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in all_subclasses(BaseWrapper)
        if cls.__module__.startswith(prefix)
    )


def indirect_subclass_report() -> list[str]:
    """`wrapper_classes_found` for a grandchild of the real wrapper (module unique to the call)."""
    from asyncopation.compile.postgresql import PostgreSQLCompileWrapper

    name = f"indirect_{uuid.uuid4().hex}"
    child = type("Child", (PostgreSQLCompileWrapper,), {"__module__": name})
    report = wrapper_classes_found(name)
    assert child  # the reference keeps the class alive while the report is taken
    return [entry.removeprefix(name + ".") for entry in report]


def diamond_subclass_report() -> list[str]:
    """`wrapper_classes_found` for a class reachable through two parents (listed once)."""
    from asyncopation.compile.postgresql import PostgreSQLCompileWrapper

    name = f"diamond_{uuid.uuid4().hex}"
    module = {"__module__": name}
    left = type("Left", (PostgreSQLCompileWrapper,), module)
    right = type("Right", (PostgreSQLCompileWrapper,), module)
    both = type("Both", (left, right), module)
    report = wrapper_classes_found(name)
    assert both  # the reference keeps the classes alive while the report is taken
    return [entry.removeprefix(name + ".") for entry in report]
