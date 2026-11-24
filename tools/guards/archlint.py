"""Architecture guard for ``src/asyncopation``.

Complements import-linter (layering, see pyproject.toml) with rules import-linter cannot express.

Codes
    A101  banned module import (sync bridges, threads, sync drivers, sync transaction API)
    A102  importing Django's thread-local connection handlers or sync transaction API
    A103  banned blocking / loop-management call
    A104  driver library imported outside ``asyncopation.drivers``
    A105  async code inside the compile-only layer
    A201  Django import not covered by docs/django-api-ledger.toml
    A202  stale ledger entry (full scans only): an [[internal]] item (not marked ``ported``)
          not imported under ``asyncopation/_django/``, or an [[attribute]] name never accessed
          as ``.name`` in src
    A203  private Django import (module not in the ledger's [public] list) outside
          ``asyncopation/_django/`` (ADR-0007 Decision 5); independent of A201

Private Django attributes accessed directly (``qs._fetch_mode``) are not detected by a guard -
register them as ledger [[attribute]] entries; the drift test protects them and arch review
catches the unregistered ones. The attribute staleness check is best-effort: it matches the
attribute name (or a string constant, for ``getattr``) anywhere in src, regardless of owner.
``from M import n`` counts as an import of ``M.n`` when that is a Django submodule.
Known limits: dynamic imports (``import_module("django...")``) and ``_private`` names taken from
a public module are not detected by A203.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

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
}
BANNED_DJANGO_DB_NAMES = {"connection", "connections", "transaction", "router"}
BANNED_CALLS = {
    "time.sleep": "blocks the event loop",
    "asyncio.run": "the library never owns the event loop",
    "asyncio.get_event_loop": "use asyncio.get_running_loop()",
    "asyncio.new_event_loop": "the library never owns the event loop",
}
BANNED_METHODS = {"run_until_complete": "the library never drives the event loop"}
DRIVER_LIBS = ("psycopg", "psycopg_pool", "asyncmy")
_ASYNC_NODES = (ast.AsyncFunctionDef, ast.Await, ast.AsyncWith, ast.AsyncFor)


def applies_to(path: str) -> bool:
    p = PurePosixPath(path)
    return p.parts[:2] == ("src", "asyncopation") and p.suffix == ".py"


_TOP_KEYS = {"public", "internal", "attribute"}
_INTERNAL_KEYS = {
    "module", "names", "used_by", "reason", "verified_against", "signatures", "ported",
}  # fmt: skip
_ATTRIBUTE_KEYS = {"owner", "names", "used_by", "reason", "verified_against", "signatures"}


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
        return cls(
            frozenset(modules),
            frozenset(internal),
            frozenset(attributes),
            frozenset(signatures),
            frozenset(attribute_signatures),
            frozenset(ported),
        )

    def covers(self, module: str, name: str) -> bool:
        """Is importing ``module.name`` recorded? Ported entries are never imported, so no."""
        importable = self.internal - self.ported
        return module in self.public or (module, name) in importable or (module, "*") in importable


def _tables(data: dict, key: str) -> list[dict]:
    tables = data.get(key, [])
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise ValueError(f"ledger: `{key}` must be an array of tables ([[{key}]])")
    return tables


def _check_keys(table: dict, allowed: set[str], kind: str) -> None:
    if unknown := sorted(set(table) - allowed):
        raise ValueError(f"{kind}: unknown key(s) {unknown} (allowed: {sorted(allowed)})")


def _check_internal_name(module: str, name: str, is_ported: bool) -> None:
    if "." in name and not is_ported:
        raise ValueError(f"[[internal]] {module}: dotted name `{name}` is only allowed if ported")
    if name != "*" and _is_django_submodule(module, name):
        raise ValueError(
            f"[[internal]] {module}: `{name}` is a submodule - "
            f'use module = "{module}.{name}" instead'
        )


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
    banned_db_name = module == "django.db" and name in BANNED_DJANGO_DB_NAMES  # A102 reports these
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

    for node in ast.walk(tree):
        for line, module, names in _imports(node):
            if hit := _banned_module(module):
                out.append(
                    Violation(
                        path, line, "A101", f"`{module}` is banned in src: {BANNED_MODULES[hit]}"
                    )
                )
            if module == "django.db" and (bad := BANNED_DJANGO_DB_NAMES & set(names)):
                out.append(
                    Violation(
                        path, line, "A102",
                        f"`from django.db import {', '.join(sorted(bad))}` - Django's sync "
                        "connection/transaction machinery must not be used; go through asyncopation.connection",
                    )
                )  # fmt: skip
            if not in_drivers and module.split(".")[0] in DRIVER_LIBS:
                out.append(
                    Violation(
                        path,
                        line,
                        "A104",
                        f"`{module}` may only be imported in asyncopation/drivers/",
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
    stale += [
        f"ledger attribute `{owner}.{name}` is never accessed in src - remove it"
        for owner, name in sorted(ledger.attributes)
        if name not in accessed
    ]
    return [Violation(LEDGER_PATH, 1, "A202", message) for message in stale]


def _in_django_package(path: str) -> bool:
    return PurePosixPath(path).parts[2:3] == ("_django",)


def _imports(node: ast.AST) -> list[tuple[int, str, list[str]]]:
    if isinstance(node, ast.Import):
        return [(node.lineno, a.name, []) for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [(node.lineno, node.module, [a.name for a in node.names])]
    return []


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
