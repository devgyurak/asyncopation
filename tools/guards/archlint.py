"""Architecture guard for ``src/asyncopation``.

Complements import-linter (layering, see pyproject.toml) with rules import-linter cannot express.

Codes
    A101  banned module import (sync bridges, threads, sync drivers, sync transaction API,
          dynamic-import helpers: ``importlib`` / ``importlib.util`` / ``importlib.machinery`` /
          ``importlib.abc`` / ``pkgutil`` / ``runpy`` / ``pydoc`` / ``builtins``)
    A102  Django's thread-local connection handlers or sync transaction API, however reached;
          the single exception and the importer restriction are described in ``connlint``
    A103  banned blocking / loop-management call, ``__import__`` / ``eval`` / ``exec`` /
          ``compile``, ``__builtins__`` and the introspection attributes ``__globals__`` /
          ``__closure__`` / ``__wrapped__`` / ``__self__`` / ``__import__`` / ``__builtins__``
    A104  driver library imported outside ``asyncopation.drivers``, including the psycopg names
          Django's modules re-export (table in ``reexports``: ``psycopg_any``, ``postgresql.base``
          ``Database`` / ``sql`` / ``adapters``, ...), whether imported (``from M import n``) or
          reached as an attribute (``M.n``, ``getattr(M, "n")``) - one ``reexports.verdict`` rule -
          and the ``.Database`` / ``.psycopg`` / ... attributes of any object; Django's adapter
          helpers only via ``_django/postgresql_adapters.py`` (importable only by
          ``drivers/postgresql``); ``mogrify`` of a Django module nowhere, a driver cursor's own
          ``.mogrify`` only in ``drivers/``
    A105  async code inside the compile-only layer
    A201  Django import not covered by docs/django-api-ledger.toml
    A202  stale ledger entry (full scans only): an [[internal]] item (not marked ``ported``)
          not imported under ``asyncopation/_django/``, or an [[attribute]] name never accessed
          as ``.name`` in src; a ``ported`` item is checked per ``used_by`` file instead - it must
          be a ``.py`` under ``src/asyncopation/``, exist in src and hold a *comment* (not a
          string or docstring) ``Ported from django/<module path>.py`` (``.../__init__.py`` for
          a package; the path directly after ``Ported from``; version and names are not compared,
          the drift test owns them)
    A203  private Django import (module not in the ledger's [public] list) outside
          ``asyncopation/_django/`` (ADR-0007 Decision 5); independent of A201

A ledger [[dependency]] row (a third-party package such as asgiref whose run-time behaviour we
rely on without importing it) is parsed and shape-checked here (exact ``X.Y.Z`` ``verified_against``,
one row per package) but is not an A201/A202 subject: nothing is imported, so nothing can be
stale; the drift test checks names, the installed version (must equal) and the pyproject requirement.

Private Django attributes accessed directly (``qs._fetch_mode``) are not detected by a guard -
register them as ledger [[attribute]] entries; the drift test protects them and arch review
catches the unregistered ones. The attribute staleness check is best-effort: it matches the
attribute name (or a string constant, for ``getattr``) anywhere in src, regardless of owner.
``from M import n`` counts as an import of ``M.n`` when that is a Django submodule.
Threat model: the guards stop code written by accident - ordinary imports, attribute access,
``getattr`` with a constant name, routes through Django or ``asyncopation`` public names - not
deliberate evasion (ADR-0008, Decision 6). Known limits, covered by architecture review:
introspection and dunder walks (``__bases__`` / ``__mro__`` / ``__subclasses__`` / ``__loader__`` /
``__spec__``, ``__dict__`` / ``vars()`` of a module, ``getattr(x, "__globals__")``,
``operator.attrgetter``, ``object.__getattribute__``, ``inspect``), ``sys.modules`` and
``from sys import modules``, ``gc`` / ``zipimport``, strings assembled at run time, calling a
guarded function through a variable (``e = eval``), ``super(CompileOnlyMixin, w).cursor()`` and
``BaseWrapper.cursor(w)`` (where the real wrapper class is importable), a class built with
``type(...)``, ``for X in (BaseWrapper,)``, a connection reached through a non-Django name
(``qs.query.get_compiler().connection.cursor()``; Django's async guards stop it at runtime),
``_private`` names taken from a public module (A203), ``BaseWrapper`` handed on through
``getattr(<asyncopation module>, "BaseWrapper")``, a star import, a call argument, ``return`` /
``yield`` or a lambda body inside ``compile/`` / ``_django/``, an ``asyncopation`` module
object held in a variable (access through it, ``m = asyncopation; m._django.connections``, is
not caught anywhere; inside ``_django/`` neither is ``x = getattr(asyncopation, "_django")``,
outside it that is A102; a name an import binds to the ``_django`` package itself, ``from
asyncopation import _django`` or ``import asyncopation._django as d``, is rejected, A102),
psycopg objects that an ``asyncopation.drivers`` module re-exports under a
public name (``from asyncopation.drivers.postgresql import sql``; ADR-0003: drivers do not do
that). Django-name tracking in ``connlint`` is per module, not per scope, so a parameter that
shadows a Django name (``def f(models)``) is treated as the Django name.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import io
import re
import tokenize
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from tools.guards import connlint, reexports
from tools.guards.core import ROOT, Violation, parse

LEDGER_PATH = "docs/django-api-ledger.toml"

BANNED_MODULES = {
    "asgiref.sync": "sync_to_async/async_to_sync hide thread hops - real async I/O only",
    "threading": "no threads: ownership is contextvars-based",
    "concurrent.futures": "no thread/process pools",
    "multiprocessing": "no process pools",
    "psycopg2": "sync driver",
    "MySQLdb": "sync driver",
    "pymysql": "sync driver",
    "django.db.transaction": "sync transaction API - use asyncopation.transaction",
    "pkgutil": "dynamic imports (resolve_name) bypass the import guards",
    "runpy": "dynamic imports bypass the import guards",
    "pydoc": "pydoc.locate imports by name and bypasses the import guards",
    "builtins": "reaching __import__ through builtins bypasses the import guards",
}
# `importlib` itself and its code-loading submodules; `importlib.metadata` / `.resources` are fine.
BANNED_IMPORTLIB = {"importlib", "importlib.util", "importlib.machinery", "importlib.abc"}
IMPORTLIB_ALLOWED_NAMES = {"metadata", "resources"}
BANNED_CALLS = {
    "time.sleep": "blocks the event loop",
    "asyncio.run": "the library never owns the event loop",
    "asyncio.get_event_loop": "use asyncio.get_running_loop()",
    "asyncio.new_event_loop": "the library never owns the event loop",
    "__import__": "dynamic imports bypass the import guards - use import statements",
    "eval": "evaluating strings bypasses the import guards",
    "exec": "executing strings bypasses the import guards",
    "compile": "compiling strings bypasses the import guards",
}
# Introspection that reaches module globals / builtins from any function or class.
BANNED_ATTRIBUTES = {
    "__globals__",
    "__closure__",
    "__wrapped__",
    "__self__",
    "__import__",
    "__builtins__",
}

BANNED_METHODS = {"run_until_complete": "the library never drives the event loop"}
DRIVER_LIBS = ("psycopg", "psycopg_pool", "asyncmy")
_ASYNC_NODES = (ast.AsyncFunctionDef, ast.Await, ast.AsyncWith, ast.AsyncFor)


def applies_to(path: str) -> bool:
    p = PurePosixPath(path)
    return p.parts[:2] == ("src", "asyncopation") and p.suffix == ".py"


_TOP_KEYS = {"public", "internal", "attribute", "dependency"}
_INTERNAL_KEYS = {
    "module", "names", "used_by", "reason", "verified_against", "signatures", "ported",
}  # fmt: skip
_ATTRIBUTE_KEYS = {"owner", "names", "used_by", "reason", "verified_against", "signatures"}
_DEPENDENCY_KEYS = {"package", "names", "used_by", "reason", "verified_against"}
_RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_DEPENDENCY_PACKAGE = re.compile(r"[a-z_][a-z0-9_]*")
_DEPENDENCY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass(frozen=True)
class Ledger:
    public: frozenset[str]
    internal: frozenset[tuple[str, str]]  # (module, name); name "*" covers `import module`
    attributes: frozenset[tuple[str, str]] = field(default_factory=frozenset)  # (owner path, name)
    signatures: frozenset[tuple[str, str, str]] = field(  # (module, dotted name, str(signature))
        default_factory=frozenset
    )
    attribute_signatures: frozenset[tuple[str, str, str]] = field(  # (owner path, name, signature)
        default_factory=frozenset
    )
    ported: frozenset[tuple[str, str]] = field(  # (module, dotted name): never imported
        default_factory=frozenset
    )
    ported_used_by: frozenset[tuple[str, str]] = field(  # (module, used_by path) of ported entries
        default_factory=frozenset
    )
    dependencies: frozenset[tuple[str, str]] = field(  # (package, dotted name below it)
        default_factory=frozenset
    )
    dependency_versions: frozenset[tuple[str, str]] = field(  # (package, verified_against)
        default_factory=frozenset
    )
    dependency_used_by: frozenset[tuple[str, str]] = field(  # (package, used_by path)
        default_factory=frozenset
    )

    @classmethod
    def load(cls) -> Ledger:
        return cls.from_toml((ROOT / LEDGER_PATH).read_text(encoding="utf-8"))

    @classmethod
    def from_toml(cls, text: str) -> Ledger:
        data = tomllib.loads(text)
        _check_keys(data, _TOP_KEYS, "ledger")
        public = data.get("public")
        if not isinstance(public, dict):
            raise ValueError("ledger: the [public] table is missing")
        _check_keys(public, {"modules"}, "[public]")
        modules = public.get("modules")
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            raise ValueError("[public] `modules` must be a list of strings")
        internal: set[tuple[str, str]] = set()
        signatures: set[tuple[str, str, str]] = set()
        ported: set[tuple[str, str]] = set()
        ported_used_by: set[tuple[str, str]] = set()
        for entry in _tables(data, "internal"):
            _check_keys(entry, _INTERNAL_KEYS, "[[internal]]")
            module = _require(entry, "[[internal]]", "module", str)
            names = _names(entry, "[[internal]]")
            is_ported = entry.get("ported", False)
            if not isinstance(is_ported, bool):
                raise ValueError(f"[[internal]] {module}: `ported` must be true or false")
            if is_ported and entry.get("signatures"):
                raise ValueError(f"[[internal]] {module}: `signatures` need an importable entry")
            for name in names:
                _check_internal_name(module, name, is_ported)
                if (module, name) in internal:
                    raise ValueError(f"[[internal]] {module}: `{name}` is listed twice")
                internal.add((module, name))
                if is_ported:
                    ported.add((module, name))
            signatures |= _signatures(entry, module, names, "[[internal]]")
            _check_record(entry, "[[internal]]", module)
            if is_ported:  # `used_by` was validated by _check_record just above
                ported_used_by |= {(module, path) for path in entry["used_by"]}
        attributes: set[tuple[str, str]] = set()
        attribute_signatures: set[tuple[str, str, str]] = set()
        for entry in _tables(data, "attribute"):
            _check_keys(entry, _ATTRIBUTE_KEYS, "[[attribute]]")
            owner = _require(entry, "[[attribute]]", "owner", str)
            names = _names(entry, "[[attribute]]")
            for name in names:
                if (owner, name) in attributes:
                    raise ValueError(f"[[attribute]] {owner}: `{name}` is listed twice")
                attributes.add((owner, name))
            attribute_signatures |= _signatures(entry, owner, names, "[[attribute]]")
            _check_record(entry, "[[attribute]]", owner)
        dependencies, dependency_versions, dependency_used_by = _dependencies(data)
        return cls(
            frozenset(modules),
            frozenset(internal),
            frozenset(attributes),
            frozenset(signatures),
            frozenset(attribute_signatures),
            frozenset(ported),
            frozenset(ported_used_by),
            frozenset(dependencies),
            frozenset(dependency_versions),
            frozenset(dependency_used_by),
        )

    def covers(self, module: str, name: str) -> bool:
        """Is importing ``module.name`` recorded? Ported entries are never imported, so no."""
        importable = self.internal - self.ported
        return module in self.public or (module, name) in importable or (module, "*") in importable


def _dependencies(
    data: dict,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """Parse the ``[[dependency]]`` rows: third-party packages the runtime behaviour rests on.

    A row records an installed package whose behaviour ``src/`` depends on without importing it
    (asgiref ``Local`` under ``compile_scope``, ADR-0008). It is deliberately *not* an A201/A202
    subject: nothing under ``src/`` imports the package, so no import can be checked, and A202
    would flag every row stale. ``tests/unit/django_drift`` owns the checks instead (names still
    resolve, the ``used_by`` files exist, installed version equals ``verified_against``, the
    pyproject requirement admits it; a row whose package ``src/`` starts importing belongs in
    ``[[internal]]``, which review catches). Hence ``verified_against`` is an exact ``X.Y.Z`` release (compared for equality), ``package`` is the
    lower-case distribution name that is also the import name (so rows cannot disagree with the
    metadata lookup or the name check), the package has one row, and ``signatures`` / ``ported``
    do not exist for this table.
    """
    names_found: set[tuple[str, str]] = set()
    versions: dict[str, str] = {}
    used_by: set[tuple[str, str]] = set()
    for entry in _tables(data, "dependency"):
        _check_keys(entry, _DEPENDENCY_KEYS, "[[dependency]]")
        package = _require(entry, "[[dependency]]", "package", str)
        names = _names(entry, "[[dependency]]")
        _check_record(entry, "[[dependency]]", package)
        if not _DEPENDENCY_PACKAGE.fullmatch(package):
            raise ValueError(
                f"[[dependency]] {package}: `package` must be the lower-case name that is both "
                "the distribution and the import name (letters, digits, `_`)"
            )
        if package == "django":
            raise ValueError(
                "[[dependency]] django: Django is covered by [[internal]] / [[attribute]] rows "
                "and the pyproject pin, not by a [[dependency]] row"
            )
        for name in names:
            if not _DEPENDENCY_NAME.fullmatch(name):
                raise ValueError(
                    f"[[dependency]] {package}: name `{name}` must be a dotted path below the "
                    'package (for example "local.Local"), never empty, `*` or with empty parts'
                )
        if not _RELEASE_VERSION.fullmatch(entry["verified_against"]):
            raise ValueError(
                f"[[dependency]] {package}: `verified_against` must be an exact release "
                f"version (X.Y.Z), got {entry['verified_against']!r}"
            )
        if package in versions:
            raise ValueError(f"[[dependency]] {package}: `{package}` is listed twice")
        versions[package] = entry["verified_against"]
        used_by |= {(package, path) for path in entry["used_by"]}
        for name in names:
            if (package, name) in names_found:
                raise ValueError(f"[[dependency]] {package}: `{name}` is listed twice")
            names_found.add((package, name))
    return names_found, set(versions.items()), used_by


def _tables(data: dict, key: str) -> list[dict]:
    tables = data.get(key, [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError(f"ledger: `{key}` must be an array of tables ([[{key}]])")
    return tables


def _check_keys(table: dict, allowed: set[str], kind: str) -> None:
    if unknown := sorted(set(table) - allowed):
        raise ValueError(f"{kind}: unknown key(s) {unknown} (allowed: {sorted(allowed)})")


def _check_internal_name(module: str, name: str, is_ported: bool) -> None:
    if is_ported and name == "*":
        raise ValueError(f"[[internal]] {module}: ported entries must name what was ported")
    if "." in name and not is_ported:
        raise ValueError(f"[[internal]] {module}: dotted name `{name}` is only allowed if ported")
    if name != "*" and _is_django_submodule(module, name):
        raise ValueError(
            f"[[internal]] {module}: `{name}` is a submodule - "
            f'use module = "{module}.{name}" instead'
        )


def _check_record(entry: dict, kind: str, owner: str) -> None:
    """Every entry says who uses it, why, and against which Django it was verified."""
    used_by = entry.get("used_by")
    if not isinstance(used_by, list) or not used_by or not all(isinstance(u, str) for u in used_by):
        raise ValueError(f"{kind} {owner}: `used_by` must be a non-empty list of strings")
    for key in ("reason", "verified_against"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ValueError(f"{kind} {owner}: `{key}` must be a non-empty string")


def _require(entry: dict, kind: str, key: str, expected: type) -> Any:
    value = entry.get(key)
    if not isinstance(value, expected):
        raise ValueError(f"{kind} entry {entry!r}: `{key}` must be a {expected.__name__}")
    return value


def _names(entry: dict, kind: str) -> list[str]:
    names = entry.get("names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError(f"{kind} entry {entry!r}: `names` must be a non-empty list of strings")
    return names


def _signatures(entry: dict, owner: str, names: list[str], kind: str) -> set[tuple[str, str, str]]:
    table = entry.get("signatures", {})
    if not isinstance(table, dict):
        raise ValueError(f"{kind} {owner}: `signatures` must be a table")
    found: set[tuple[str, str, str]] = set()
    for name, signature in table.items():
        if not isinstance(signature, str):
            raise ValueError(
                f"{kind} {owner}: signature of `{name}` must be a string - quote dotted keys: "
                f'signatures = {{ "Query.get_compiler" = "(...)" }}'
            )
        if name.split(".")[0] not in names:
            raise ValueError(f"{kind} {owner}: signature `{name}` is not covered by `names`")
        found.add((owner, name, signature))
    return found


def django_imports(tree: ast.Module) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
            and _is_django(node.module)
        ):
            found += [_resolve_from_import(node.lineno, node.module, a.name) for a in node.names]
        elif isinstance(node, ast.Import):
            found += [(node.lineno, a.name, "*") for a in node.names if _is_django(a.name)]
    return found


def _resolve_from_import(line: int, module: str, name: str) -> tuple[int, str, str]:
    """``from M import n`` where ``M.n`` is a Django submodule is an import of ``M.n``."""
    banned_db_name = module == "django.db" and name in connlint.BANNED_NAMES  # A102 reports these
    if not banned_db_name and _is_django_submodule(module, name):
        return (line, f"{module}.{name}", "*")
    return (line, module, name)


@functools.cache
def _django_root() -> Path | None:
    spec = importlib.util.find_spec("django")
    return (
        Path(spec.submodule_search_locations[0])
        if spec and spec.submodule_search_locations
        else None
    )


def _is_django_submodule(module: str, name: str) -> bool:
    root = _django_root()
    if root is None:
        raise RuntimeError(
            "django is not importable - run `uv sync` (the A203 submodule check needs it)"
        )
    if name == "*":
        return False
    package = root.parent.joinpath(*module.split("."))
    # Exact-case listing: `Query` must not match `query.py` on case-insensitive filesystems.
    entries = {entry.name for entry in package.iterdir()} if package.is_dir() else set()
    return name in entries or f"{name}.py" in entries


def check(path: str, source: str, ledger: Ledger | None = None) -> list[Violation]:
    tree = parse(source, path)
    if isinstance(tree, Violation):
        return [tree]
    ledger = ledger or Ledger.load()
    out: list[Violation] = []
    in_drivers = PurePosixPath(path).parts[2:3] == ("drivers",)
    in_compile = PurePosixPath(path).parts[2:3] == ("compile",)
    in_django = _in_django_package(path)
    out += connlint.check(path, tree, _is_django_submodule)
    module_paths = connlint.module_paths(tree, _is_django_submodule)

    for node in ast.walk(tree):
        for line, module, names in _imports(node):
            if hit := _banned_module(module):
                out.append(
                    Violation(
                        path, line, "A101", f"`{module}` is banned in src: {BANNED_MODULES[hit]}"
                    )
                )
            if _banned_importlib(module, names):
                out.append(
                    Violation(
                        path, line, "A101",
                        f"`{module}` is banned in src: dynamic imports bypass the import guards",
                    )
                )  # fmt: skip
            out += _driver_import(path, line, module, names, in_drivers, node)
            if not in_drivers and module.split(".")[0] in DRIVER_LIBS:
                out.append(
                    Violation(
                        path,
                        line,
                        "A104",
                        f"`{module}` may only be imported in asyncopation/drivers/",
                    )
                )
        if isinstance(node, ast.Import):
            out += [
                Violation(
                    path,
                    node.lineno,
                    "A101",
                    f"`import {a.name}` also binds `importlib`; use `from importlib import "
                    f"{a.name.split('.')[-1]}`",
                )
                for a in node.names
                if a.name in ("importlib.metadata", "importlib.resources") and not a.asname
            ]
        out += _driver_attribute(path, node, module_paths, in_drivers)
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRIBUTES:
            out.append(
                Violation(
                    path,
                    node.lineno,
                    "A103",
                    f"`.{node.attr}` reaches module globals / builtins and bypasses the import "
                    "guards",
                )
            )
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            out.append(
                Violation(
                    path, node.lineno, "A103", "`__builtins__` reaches `__import__` dynamically"
                )
            )
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in BANNED_CALLS:
                out.append(Violation(path, node.lineno, "A103", f"`{name}()` {BANNED_CALLS[name]}"))
            method = name.rsplit(".", 1)[-1]
            if method in BANNED_METHODS:
                out.append(
                    Violation(path, node.lineno, "A103", f"`.{method}()` {BANNED_METHODS[method]}")
                )
        if in_compile and isinstance(node, _ASYNC_NODES):
            out.append(
                Violation(
                    path, node.lineno, "A105", "compile layer must stay synchronous and I/O-free"
                )
            )

    out += [
        Violation(
            path, line, "A203",
            f"`{module}{'' if name == '*' else '.' + name}` is private Django API - import it only "
            "inside asyncopation/_django/ and expose it from there",
        )
        for line, module, name in django_imports(tree)
        if not in_django and module not in ledger.public
    ]  # fmt: skip
    out += [
        Violation(
            path, line, "A201",
            f"`{module}{'' if name == '*' else '.' + name}` is a Django internal not recorded in "
            f"{LEDGER_PATH} - add an [[internal]] entry (see .agents/skills/port-django-internals)",
        )
        for line, module, name in django_imports(tree)
        if not ledger.covers(module, name)
    ]  # fmt: skip
    return out


def stale_ledger_entries(sources: dict[str, str], ledger: Ledger | None = None) -> list[Violation]:
    ledger = ledger or Ledger.load()
    imported: set[tuple[str, str]] = set()
    accessed: set[str] = set()
    for path, src in sources.items():
        if isinstance(tree := parse(src, path), Violation):
            continue
        accessed |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        accessed |= {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }  # getattr(o, "name")
        if _in_django_package(path):
            imported |= {(module, name) for _, module, name in django_imports(tree)}
            for node in ast.walk(tree):  # non-Django third parties (asgiref.local) too
                for _, module, names in _imports(node):
                    imported |= {(module, name) for name in names or ["*"]}
    plain = {module for module, name in imported if name == "*"}
    from_imported = {module for module, _ in imported}
    stale = [
        f"ledger entry `{module}.{name}` is not imported under asyncopation/_django/ - move the "
        "import there, mark the entry `ported = true` if it is only ported, or remove it"
        for module, name in sorted(ledger.internal - ledger.ported)
        if (module, name) not in imported
        and module not in plain
        and not (name == "*" and module in from_imported)
    ]
    stale += _stale_ported_entries(ledger, sources)
    stale += [
        f"ledger attribute `{owner}.{name}` is never accessed in src - remove it"
        for owner, name in sorted(ledger.attributes)
        if name not in accessed
    ]
    return [Violation(LEDGER_PATH, 1, "A202", message) for message in stale]


_PORTED_FROM = re.compile(r"\bPorted from (django/[\w/]+\.py)(?!\w|[/-]|\.\w)")
_SRC_PREFIX = "src/asyncopation/"


def _stale_ported_entries(ledger: Ledger, sources: dict[str, str]) -> list[str]:
    """Each (ported module, used_by file) needs the file to exist and carry a source header."""
    out: list[str] = []
    for module, path in sorted(ledger.ported_used_by):
        header = f"`# Ported from {module.replace('.', '/')}.py::<name> @ <version>`"
        if not (path.startswith(_SRC_PREFIX) and path.endswith(".py")):
            out.append(
                f"ported ledger entry `{module}`: used_by `{path}` is not a .py file under "
                f"{_SRC_PREFIX} - point it at the file that holds the port"
            )
        elif path not in sources:
            out.append(
                f"ported ledger entry `{module}`: used_by `{path}` does not exist in src - fix "
                "the path or remove it from used_by"
            )
        elif not _has_ported_header(sources[path], module):
            out.append(
                f"ported ledger entry `{module}`: `{path}` has no comment {header} - add it "
                "directly above the ported code (the django/ path must follow `Ported from` on "
                "the same line, as a comment, not in a string or docstring)"
            )
    return out


def _has_ported_header(source: str, module: str) -> bool:
    """Is there a ``# Ported from django/<module path>.py`` comment (package: ``__init__.py``)?"""
    base = module.replace(".", "/")
    accepted = {f"{base}.py", f"{base}/__init__.py"}
    try:
        comments = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return False
    return any(m.group(1) in accepted for text in comments for m in _PORTED_FROM.finditer(text))


def _in_django_package(path: str) -> bool:
    return PurePosixPath(path).parts[2:3] == ("_django",)


def _imports(node: ast.AST) -> list[tuple[int, str, list[str]]]:
    if isinstance(node, ast.Import):
        return [(node.lineno, a.name, []) for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [(node.lineno, node.module, [a.name for a in node.names])]
    return []


def _driver_import(
    path: str, line: int, module: str, names: list[str], in_drivers: bool, node: ast.AST
) -> list[Violation]:
    """A104 for ``import M`` / ``from M import n``: the (module, name) verdict of ``reexports``."""
    if isinstance(node, ast.Import):  # only the psycopg_any module itself is a driver name
        names = ["*"] if module.startswith(reexports.PSYCOPG_ANY) else []
    return [
        v
        for name in names
        if (v := _driver_violation(path, line, module, name, in_drivers, attribute=False))
    ]


def _driver_attribute(
    path: str, node: ast.AST, module_paths: dict[str, str], in_drivers: bool
) -> list[Violation]:
    """A104 for ``M.n`` and ``getattr(M, "n")``: the same verdict as the import form."""
    getattr_name = _const_getattr_name(node)
    if isinstance(node, ast.Attribute):
        base, name = node.value, node.attr
    elif isinstance(node, ast.Call) and getattr_name is not None:
        base, name = node.args[0], getattr_name
    else:
        return []
    module = connlint.module_path_of(base, module_paths, _is_django_submodule) or ""
    found = _driver_violation(
        path, getattr(node, "lineno", 1), module, name, in_drivers, attribute=True
    )
    return [found] if found else []


def _driver_violation(
    path: str, line: int, module: str, name: str, in_drivers: bool, *, attribute: bool
) -> Violation | None:
    kind = reexports.verdict(module, name, attribute=attribute)
    if kind == reexports.EVERYWHERE:
        message = (
            f"`{name}` of a Django module renders through a cursor (I/O); "
            "it is forbidden everywhere"
        )
    elif kind == reexports.DRIVER and name in reexports.FORBIDDEN_EVERYWHERE and not in_drivers:
        message = (
            f"`.{name}` is a driver cursor method; it may only be used in asyncopation/drivers/"
        )
    elif kind == reexports.DRIVER and not in_drivers:
        message = (
            f"`.{name}` reaches the psycopg driver (the psycopg module itself or a name Django "
            "re-exports from it); it may only be used in asyncopation/drivers/"
            if attribute
            else f"`{module}` re-exports the psycopg driver ({name}); it may only be used in "
            "asyncopation/drivers/"
        )
    elif kind == reexports.HELPER and path != reexports.ADAPTER_BOUNDARY:
        message = (
            f"`{name}` may only be imported by {reexports.ADAPTER_BOUNDARY} "
            "(drivers use it from there)"
        )
    else:
        return None
    return Violation(path, line, "A104", message)


def _const_getattr_name(node: ast.AST) -> str | None:
    """``name`` in ``getattr(x, "name"[, default])``, else ``None``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _banned_importlib(module: str, names: list[str]) -> bool:
    if module in BANNED_IMPORTLIB:
        # `from importlib import metadata` is fine; `import importlib` / `import_module` are not
        return module != "importlib" or not names or not set(names) <= IMPORTLIB_ALLOWED_NAMES
    return False


def _banned_module(module: str) -> str | None:
    return next((b for b in BANNED_MODULES if module == b or module.startswith(b + ".")), None)


def _is_django(module: str) -> bool:
    return module == "django" or module.startswith("django.")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""
