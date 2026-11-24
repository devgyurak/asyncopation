"""Architecture guard for ``src/asyncopation``.

Complements import-linter (layering, see pyproject.toml) with rules import-linter cannot express.

Codes
    A101  banned module import (sync bridges, threads, sync drivers, sync transaction API)
    A102  importing Django's thread-local connection handlers or sync transaction API
    A103  banned blocking / loop-management call
    A104  driver library imported outside ``asyncopation.drivers``
    A105  async code inside the compile-only layer
    A201  Django import not covered by docs/django-api-ledger.toml
    A202  stale ledger entry (full scans only)
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

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


@dataclass(frozen=True)
class Ledger:
    public: frozenset[str]
    internal: frozenset[tuple[str, str]]  # (module, name); name "*" covers `import module`

    @classmethod
    def load(cls) -> Ledger:
        data = tomllib.loads((ROOT / LEDGER_PATH).read_text())
        internal = {
            (entry["module"], name)
            for entry in data.get("internal", [])
            for name in entry.get("names", ["*"])
        }
        return cls(frozenset(data["public"]["modules"]), frozenset(internal))

    def covers(self, module: str, name: str) -> bool:
        return (
            module in self.public
            or (module, name) in self.internal
            or (module, "*") in self.internal
        )


def django_imports(tree: ast.Module) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
            and _is_django(node.module)
        ):
            found += [(node.lineno, node.module, a.name) for a in node.names]
        elif isinstance(node, ast.Import):
            found += [(node.lineno, a.name, "*") for a in node.names if _is_django(a.name)]
    return found


def check(path: str, source: str, ledger: Ledger | None = None) -> list[Violation]:
    tree = parse(source, path)
    if isinstance(tree, Violation):
        return [tree]
    ledger = ledger or Ledger.load()
    out: list[Violation] = []
    in_drivers = PurePosixPath(path).parts[2:3] == ("drivers",)
    in_compile = PurePosixPath(path).parts[2:3] == ("compile",)

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
            path, line, "A201",
            f"`{module}{'' if name == '*' else '.' + name}` is a Django internal not recorded in "
            f"{LEDGER_PATH} - add an [[internal]] entry (see .agents/skills/port-django-internals)",
        )
        for line, module, name in django_imports(tree)
        if not ledger.covers(module, name)
    ]  # fmt: skip
    return out


def stale_ledger_entries(sources: dict[str, str]) -> list[Violation]:
    ledger = Ledger.load()
    used = {
        (module, name)
        for path, src in sources.items()
        if not isinstance(tree := parse(src, path), Violation)
        for _, module, name in django_imports(tree)
    }
    return [
        Violation(
            LEDGER_PATH,
            1,
            "A202",
            f"ledger entry `{module}.{name}` is not used in src anymore - remove it",
        )
        for module, name in sorted(ledger.internal)
        if (module, name) not in used
    ]


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
