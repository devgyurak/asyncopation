"""Which psycopg names do the installed Django's PostgreSQL modules bind? (drift of reexports.py)"""

from __future__ import annotations

import ast
from pathlib import Path

import django
import pytest

from tools.guards.reexports import ADAPTER_HELPERS, ALLOWED_NAMES, DRIVER_REEXPORTS, PSYCOPG_ANY

DRIVER_PACKAGES = ("psycopg", "psycopg2", "psycopg_pool", "MySQLdb")
SCANNED = (
    "django.db.backends.postgresql",
    "django.contrib.postgres",
    "django.contrib.gis.db.backends.postgis",
    "django.db.backends.mysql",
    "django.contrib.gis.db.backends.mysql",
)


def _is_driver(module: str) -> bool:
    return module.split(".")[0] in DRIVER_PACKAGES


def _is_psycopg_any(module: str, level: int) -> bool:
    return module == PSYCOPG_ANY or (level > 0 and module.split(".")[-1] == "psycopg_any")


def bound_driver_names(
    source: str, module: str = "", table: dict[str, frozenset[str]] | None = None
) -> set[str]:
    """Local names bound from the driver, from `psycopg_any` or from a listed re-exporting module.

    Any scope. A name taken from a module the table lists (``from .base import Database``) is a
    re-export too; ``module`` (the importing module's dotted name) resolves relative imports.
    Only imports bind names here: a module-level assignment rooted in a driver name (``Binary =
    Database.Binary``) is not followed (Django has one, ``mysql.base``'s ``version =
    Database.version_info``, which is harmless); a new one needs a table row by hand.
    """
    table = DRIVER_REEXPORTS if table is None else table
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            found |= _from_import_names(node, module, table)
        elif isinstance(node, ast.Import):
            found |= {
                (alias.asname or alias.name.split(".")[0])
                for alias in node.names
                if _is_driver(alias.name) or alias.name == PSYCOPG_ANY
            }
    return found


def _from_import_names(
    node: ast.ImportFrom, module: str, table: dict[str, frozenset[str]]
) -> set[str]:
    target = _resolve(node, module)
    if _is_driver_import(node):
        return {(alias.asname or alias.name) for alias in node.names}
    if f"{target}.psycopg_any" == PSYCOPG_ANY:
        return {(alias.asname or alias.name) for alias in node.names if alias.name == "psycopg_any"}
    listed = table.get(target)
    if listed is None or target == PSYCOPG_ANY:
        return set()
    return {(a.asname or a.name) for a in node.names if a.name in listed or a.name == "*"}


def _resolve(node: ast.ImportFrom, module: str) -> str:
    if not node.level:
        return node.module or ""
    package = module.split(".")[: -node.level] if module else []
    return ".".join([*package, *([node.module] if node.module else [])])


def _is_driver_import(node: ast.ImportFrom) -> bool:
    module = node.module or ""
    return (node.level == 0 and _is_driver(module)) or _is_psycopg_any(module, node.level)


def django_modules() -> dict[str, Path]:
    """Module name -> source file for every scanned Django package."""
    root = Path(django.__file__).parent
    found: dict[str, Path] = {}
    for package in SCANNED:
        base = root.joinpath(*package.split(".")[1:])
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root.parent).with_suffix("")
            name = ".".join(relative.parts).removesuffix(".__init__")
            found[name] = path
    return found


def unlisted_driver_names(module: str, table: dict[str, frozenset[str]] | None = None) -> list[str]:
    """Driver names `module` binds that neither the table nor the allow-lists cover."""
    table = DRIVER_REEXPORTS if table is None else table
    if "*" in table.get(module, frozenset()):
        return []
    source = django_modules()[module].read_text()
    return sorted(
        bound_driver_names(source, module, table)
        - table.get(module, frozenset())
        - ALLOWED_NAMES
        - ADAPTER_HELPERS
    )


MODULES = django_modules()
MODULE_PARAMS = [pytest.param(name, id=name.removeprefix("django.")) for name in sorted(MODULES)]


def table_without(module: str) -> dict[str, frozenset[str]]:
    return {k: v for k, v in DRIVER_REEXPORTS.items() if k != module}
