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


def default_wrapper_packages() -> list[str]:
    """The packages whose modules may define wrapper classes."""
    return ["asyncopation.compile", "asyncopation._django"]


def import_every_wrapper_module(packages: list[str] | None = None) -> None:
    """Import every module of the packages (they register their wrapper classes). A module whose
    driver library is missing (ImportError, ImproperlyConfigured) is skipped; any other error is
    not hidden."""
    import importlib
    import pkgutil

    from django.core.exceptions import ImproperlyConfigured

    for name in default_wrapper_packages() if packages is None else packages:
        package = importlib.import_module(name)
        for info in pkgutil.iter_modules(package.__path__, name + "."):
            try:
                importlib.import_module(info.name)
            except (ImportError, ImproperlyConfigured):
                continue


def _write_scanned_package(directory: Any, name: str | None = None) -> str:
    """A throwaway package (unique name) with wrapper classes of several Django backends and two
    modules whose driver is missing. Its classes stay registered as `BaseDatabaseWrapper`
    subclasses for the rest of the process; the production check ignores them (module prefix)."""
    name = name or f"scanned_{uuid.uuid4().hex}"
    package = directory / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "wrappers.py").write_text(
        "from django.db.backends.base.base import BaseDatabaseWrapper\n"
        "from django.db.backends.sqlite3.base import DatabaseWrapper as Sqlite\n"
        "from asyncopation.compile.wrapper import CompileOnlyMixin\n\n\n"
        "class Bare(BaseDatabaseWrapper):\n    pass\n\n\n"
        "class OnSqlite(Sqlite):\n    pass\n\n\n"
        "class Safe(CompileOnlyMixin, Sqlite):\n    pass\n"
    )
    (package / "no_driver.py").write_text("raise ImportError('no driver')\n")
    (package / "bad_config.py").write_text(
        "from django.core.exceptions import ImproperlyConfigured\n"
        "raise ImproperlyConfigured('no driver')\n"
    )
    return name


def _forget(name: str) -> None:
    import sys

    for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[loaded]


def scanned_package_report(directory: Any) -> tuple[list[str], list[str], list[str]]:
    """Scan a throwaway package: (classes that can connect, classes found, error kinds raised)."""
    import importlib
    import sys

    name = _write_scanned_package(directory)
    sys.path.insert(0, str(directory))
    try:
        import_every_wrapper_module([name])
        reported = wrapper_classes_that_can_connect(name, scan=False)
        found = wrapper_classes_found(name, scan=False)
        (directory / name / "crash.py").write_text("raise RuntimeError('boom')\n")
        importlib.invalidate_caches()
        try:
            import_every_wrapper_module([name])
        except RuntimeError:
            raised = ["RuntimeError"]
        else:
            raised = []
    finally:
        sys.path.remove(str(directory))
        _forget(name)
    strip = name + ".wrappers."
    return (
        [entry.removeprefix(strip) for entry in reported],
        sorted(entry.removeprefix(strip) for entry in found),
        raised,
    )


def default_scan_report(directory: Any) -> tuple[list[str], list[str], list[str]]:
    """The default scan covering two throwaway packages: (classes listed before any scan, classes
    listed after it, throwaway modules left in `sys.modules` afterwards)."""
    import sys
    from unittest import mock

    tag = f"scanned_{uuid.uuid4().hex}"
    names = [_write_scanned_package(directory, f"{tag}_{suffix}") for suffix in ("a", "b")]
    sys.path.insert(0, str(directory))
    try:
        with mock.patch(f"{__name__}.default_wrapper_packages", return_value=names):
            before = wrapper_classes_found(tag, scan=False)
            after = wrapper_classes_found(tag)
    finally:
        sys.path.remove(str(directory))
        for name in names:
            _forget(name)
    left = [m for m in sys.modules if m.startswith(tag)]
    return before, sorted(entry.replace(tag + "_", "") for entry in after), left


def wrapper_classes_that_can_connect(
    prefix: str = "asyncopation.", *, scan: bool = True
) -> list[str]:
    """Classes (module starting with `prefix`) based on any Django backend wrapper, directly or
    not, without the compile-only mixin before the backend class in the MRO."""
    from django.db.backends.base.base import BaseDatabaseWrapper

    from asyncopation.compile.wrapper import CompileOnlyMixin

    if scan:
        import_every_wrapper_module()
    return sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in all_subclasses(BaseDatabaseWrapper)
        if cls.__module__.startswith(prefix) and not _mixin_first(cls, CompileOnlyMixin)
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


def _mixin_first(cls: type, mixin: type) -> bool:
    """The mixin is in the MRO before the first class that Django itself provides."""
    from django.db.backends.base.base import BaseDatabaseWrapper

    mro = cls.__mro__
    djangos = [
        i
        for i, c in enumerate(mro)
        if issubclass(c, BaseDatabaseWrapper) and c.__module__.startswith("django.")
    ]
    return mixin in mro and mro.index(mixin) < min(djangos)


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


def wrapper_classes_found(prefix: str = "asyncopation.", *, scan: bool = True) -> list[str]:
    """Classes (module starting with `prefix`) based on a Django backend wrapper, however
    indirectly."""
    from django.db.backends.base.base import BaseDatabaseWrapper

    if scan:
        import_every_wrapper_module()
    return sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in all_subclasses(BaseDatabaseWrapper)
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
