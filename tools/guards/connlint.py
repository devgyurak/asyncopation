"""A102 - Django's sync connection / transaction machinery in ``src/`` (ADR-0008, Decision 6).

Used by ``archlint``; kept apart because the rule set is large. What is rejected, in every
``src`` module:

- importing ``connection`` / ``connections`` / ``transaction`` / ``router`` /
  ``close_old_connections`` / ``reset_queries`` (and the names in ``BANNED_NAMES``) from *any*
  Django module (so a public module that re-exports the handler is no way around),
  ``from django import db`` and star imports from Django, ``import django.db as d``;
- attribute access of those names (``django.db.models.base.connections``, ``m.query.connections``)
  or ``getattr(<django name>, "connections")`` on a name bound by a Django import or assigned
  from one (aliases and parameter defaults are tracked to a fixed point; a ``getattr`` with a
  computed name on such an object is rejected too);
- using a Django *module* object (``models``, ``django``) as a value instead of as the base of an
  attribute chain (unpacking, ``for`` targets, containers, arguments, returns, class attributes):
  ``models.base.connections`` could ride along;
- importing ``asyncopation._django.connections`` anywhere but ``compile/scope.py`` (which may only
  import ``swapped``), importing the ``asyncopation._django`` package object, using
  ``asyncopation._django.connections`` or ``swapped`` / ``_swapped`` anywhere else;
- importing Django's real PostgreSQL wrapper class (``BaseWrapper``, from any module) outside
  ``compile/`` and ``_django/``, renaming it on import (``BaseWrapper as X``) or rebinding it
  (``X = BaseWrapper``, also in containers, conditional expressions, ``or``, ``:=`` and parameter
  defaults)
  anywhere: the rules follow it by name; a class based on it lists ``CompileOnlyMixin`` first
  (otherwise ``BaseWrapper.cursor`` wins in the MRO), and it is never called (``BaseWrapper(...)``
  connects);
- importing a backend's ``DatabaseWrapper`` (``django[.contrib.gis].db.backends.*.base``) other
  than as ``BaseWrapper`` from ``_django/postgresql.py`` / ``_django/mysql.py``
  (``WRAPPER_BOUNDARIES``), also when reached as an attribute (or constant ``getattr``) of a
  Django name;
- the Django names ``ConnectionHandler``, ``ConnectionRouter``, ``BaseConnectionHandler`` and
  ``load_backend`` are banned like ``connections`` (``BANNED_NAMES``);
- importing ``asyncopation._django.postgresql_adapters`` anywhere but ``drivers/postgresql`` (A104,
  the boundary for Django's adapter helpers, used by the psycopg driver).

One module is exempt: ``src/asyncopation/_django/connections.py`` may bind ``connections`` inside a
function and use it only as ``connections[alias] = value``, ``del connections[alias]`` and
``x = getattr(connections._connections, alias, default)``. The peeked value ``x`` may only be
compared with ``is`` / ``is not`` or stored back as ``connections[alias] = x``; no sync connection
operation (``.cursor()``, ``.connect()``, ...) may appear in the module.

The guard is static and best-effort (see ``archlint``'s known limits).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import PurePosixPath

from tools.guards.core import Violation

BANNED_NAMES = frozenset(
    {
        "connection",
        "connections",
        "transaction",
        "router",
        "close_old_connections",
        "reset_queries",
        "ConnectionHandler",
        "ConnectionRouter",
        "BaseConnectionHandler",
        "load_backend",
    }
)
CONNECTIONS_MODULE = "src/asyncopation/_django/connections.py"
CONNECTIONS_IMPORTER = "src/asyncopation/compile/scope.py"
PACKAGE = "asyncopation._django"
CONNECTIONS_TARGET = f"{PACKAGE}.connections"
POSTGRESQL_TARGET = f"{PACKAGE}.postgresql"
ADAPTER_TARGET = f"{PACKAGE}.postgresql_adapters"
SWAP_NAMES = frozenset({"swapped", "_swapped"})
# The only modules that may import a backend's real `DatabaseWrapper`, and only as `BaseWrapper`.
WRAPPER_BOUNDARIES = frozenset(
    {"src/asyncopation/_django/postgresql.py", "src/asyncopation/_django/mysql.py"}
)
OPERATIONS = frozenset(
    {
        "cursor",
        "chunked_cursor",
        "create_cursor",
        "ensure_connection",
        "connect",
        "_cursor",
        "get_new_connection",
        "temporary_connection",
        "close",
        "close_if_unusable_or_obsolete",
        "commit",
        "_commit",
        "rollback",
        "_rollback",
        "savepoint",
        "set_autocommit",
        "connection",
    }
)


_INTROSPECTION = frozenset({"locals", "vars", "globals", "dir", "exec", "eval"})
_FRAME_ATTRIBUTES = frozenset({"_getframe", "currentframe", "f_locals", "f_globals"})


def check(
    path: str, tree: ast.Module, is_module: Callable[[str, str], bool] | None = None
) -> list[Violation]:
    """``is_module(package, name)`` tells whether ``package.name`` is a Django submodule."""
    return _Checker(path, tree, is_module or (lambda _module, _name: False)).run()


class _Checker:
    def __init__(self, path: str, tree: ast.Module, is_module: Callable[[str, str], bool]) -> None:
        self.is_module = is_module
        self.path = path
        self.tree = tree
        self.parts = PurePosixPath(path).parts
        self.exempt = path == CONNECTIONS_MODULE
        self.importer = path == CONNECTIONS_IMPORTER
        self.in_compile = self.parts[:3] == ("src", "asyncopation", "compile")
        self.in_django = self.parts[:3] == ("src", "asyncopation", "_django")
        self.in_pg_driver = self.parts[:3] == ("src", "asyncopation", "drivers") and self.parts[
            3:4
        ] in (("postgresql",), ("postgresql.py",))
        self.out: list[Violation] = []
        self.parents = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        self.roots = _django_roots(tree)
        self.module_paths = module_paths(tree, is_module)
        self.package_roots = _asyncopation_roots(tree)
        self.modules = frozenset(self.module_paths)
        self.in_function = {
            id(n)
            for f in ast.walk(tree)
            if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
            for n in ast.walk(f)
        }

    def flag(self, node: ast.AST, message: str, code: str = "A102") -> None:
        self.out.append(Violation(self.path, getattr(node, "lineno", 1), code, message))

    def run(self) -> list[Violation]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self._import(node)
            elif isinstance(node, ast.ImportFrom):
                self._import_from(node)
            elif isinstance(node, ast.Attribute):
                self._attribute(node)
            elif isinstance(node, ast.Call):
                self._getattr_call(node)
                self._wrapper_call(node)
            elif isinstance(node, ast.ClassDef):
                self._wrapper_subclass(node)
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr):
                self._wrapper_rebinding(node)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                self._wrapper_defaults(node)
            elif isinstance(node, ast.Name):
                self._module_as_value(node)
        if self.exempt:
            self._exempt_module()
        return self.out

    def _module_as_value(self, node: ast.Name) -> None:
        """A Django module object (``models``, ``django``) may only be used as an attribute base.

        Anywhere else (assignment, unpacking, ``for`` target, argument, return, container) the
        module could carry ``models.base.connections`` past the alias tracking.
        """
        if node.id not in self.modules or not isinstance(node.ctx, ast.Load):
            return
        parent = self.parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "getattr"
            and parent.args[:1] == [node]
            and len(parent.args) >= 2
            and isinstance(parent.args[1], ast.Constant)
        ):
            return
        self.flag(
            node,
            f"Django module `{node.id}` used as a value: it re-exports connection machinery "
            "(`models.base.connections`) - use attribute access only",
        )

    # imports -------------------------------------------------------------------------------
    def _import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname and alias.name in ("django", "django.db"):
                self.flag(node, f"`import {alias.name} as {alias.asname}` hides `db.connections`")
            if alias.name == PACKAGE and alias.asname:
                self.flag(node, "do not bind the `asyncopation._django` package object")
            if _under(alias.name, CONNECTIONS_TARGET):
                self._connections_importer(node, {"*"})
            if _under(alias.name, ADAPTER_TARGET):
                self._adapters_importer(node)

    def _import_from(self, node: ast.ImportFrom) -> None:
        names = {a.name for a in node.names}
        base = _resolve(self.path, node)
        if node.level == 0 and (node.module or "").split(".")[0] == "django":
            self._django_names(node, names)
        if base == "asyncopation" and "_django" in names:
            self.flag(node, "do not bind the `asyncopation._django` package object")
        if base == PACKAGE and names & {"connections", "*"}:
            self.flag(
                node, "`asyncopation._django.connections` is reachable only from compile/scope.py"
            )
        if _under(base, CONNECTIONS_TARGET) or (base == PACKAGE and "connections" in names):
            self._connections_importer(node, names)
        if _under(base, ADAPTER_TARGET) or (base == PACKAGE and "postgresql_adapters" in names):
            self._adapters_importer(node)
        if names & SWAP_NAMES and not (self.importer or self.exempt):
            self.flag(node, "`swapped` is private to compile/scope.py (ADR-0008, Decision 6)")
        if _is_backend_base(base):
            for alias in node.names:
                if alias.name == "DatabaseWrapper" and not (
                    self.path in WRAPPER_BOUNDARIES and alias.asname == "BaseWrapper"
                ):
                    self.flag(
                        node,
                        "Django's real `DatabaseWrapper` may only be imported as `BaseWrapper` by "
                        "the `_django` backend modules (postgresql.py, mysql.py)",
                    )
        if ((base == POSTGRESQL_TARGET and "*" in names) or "BaseWrapper" in names) and not (
            self.in_compile or self.in_django
        ):
            self.flag(
                node, "Django's real PostgreSQL wrapper class may only be imported by compile/"
            )
        if any(
            a.name == "BaseWrapper" and a.asname not in (None, "BaseWrapper") for a in node.names
        ):
            self.flag(node, "do not rename `BaseWrapper`: the rules follow it by name")

    def _wrapper_rebinding(self, node: ast.Assign | ast.AnnAssign | ast.NamedExpr) -> None:
        """``X = BaseWrapper`` would hand the real wrapper class to code the rules cannot follow."""
        if node.value is not None and _forwards_wrapper(node.value):
            self.flag(node, "do not rebind `BaseWrapper`: the rules follow it by name")

    def _wrapper_subclass(self, node: ast.ClassDef) -> None:
        """A class based on ``BaseWrapper`` must put ``CompileOnlyMixin`` before it (MRO)."""
        names = [_class_name(b) for b in node.bases]
        if "BaseWrapper" in names and not (
            "CompileOnlyMixin" in names
            and names.index("CompileOnlyMixin") < names.index("BaseWrapper")
        ):
            self.flag(
                node,
                "a class based on `BaseWrapper` must list `CompileOnlyMixin` before it: "
                "otherwise `BaseWrapper.cursor` wins and the wrapper can connect",
            )

    def _wrapper_call(self, node: ast.Call) -> None:
        if _class_name(node.func) == "BaseWrapper":
            self.flag(node, "do not instantiate `BaseWrapper`: it connects; use `wrapper_for`")

    def _wrapper_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
        if any(_forwards_wrapper(d) for d in defaults):
            self.flag(node, "do not rebind `BaseWrapper`: the rules follow it by name")

    def _adapters_importer(self, node: ast.AST) -> None:
        if not self.in_pg_driver:
            self.flag(
                node,
                "`asyncopation._django.postgresql_adapters` may only be imported by "
                "asyncopation/drivers/postgresql",
                "A104",
            )

    def _django_names(self, node: ast.ImportFrom, names: set[str]) -> None:
        if node.module == "django" and "db" in names:
            self.flag(node, "`from django import db` hides `db.connections` - import what you need")
        if "*" in names:
            self.flag(node, f"`from {node.module} import *` hides banned names")
        allowed_here = (
            {"connections"}
            if self.exempt and id(node) in self.in_function and not _aliased(node)
            else set()
        )
        if bad := (BANNED_NAMES & names) - allowed_here:
            self.flag(
                node,
                f"`from {node.module} import {', '.join(sorted(bad))}` - Django's sync "
                "connection/transaction machinery must not be used; go through "
                "asyncopation.connection",
            )

    def _connections_importer(self, node: ast.AST, names: set[str]) -> None:
        if self.exempt:
            return
        ok = (
            self.importer
            and isinstance(node, ast.ImportFrom)
            and _resolve(self.path, node) == CONNECTIONS_TARGET
            and names <= {"swapped", "ScopeContextError"}
        )
        if not ok:
            self.flag(
                node,
                "`asyncopation._django.connections` may only be imported by compile/scope.py "
                "and only for `swapped` (ADR-0008, Decision 6)",
            )

    # attributes and getattr -----------------------------------------------------------------
    def _module_chain_as_value(self, node: ast.Attribute) -> None:
        """``models.base`` (a Django submodule reached by attributes) used as a value."""
        parent = self.parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return  # part of a longer chain, judged at its end
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not (isinstance(current, ast.Name) and current.id in self.module_paths):
            return
        path = self.module_paths[current.id]
        for attr in reversed(parts):
            if not self.is_module(path, attr):
                return  # a class, function or constant: not a module object
            path = f"{path}.{attr}"
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "getattr"
            and parent.args[:1] == [node]
            and len(parent.args) >= 2
            and isinstance(parent.args[1], ast.Constant)
        ):
            return
        self.flag(
            node,
            f"Django module `{path}` used as a value: it re-exports connection machinery - "
            "use attribute access only",
        )

    def _attribute(self, node: ast.Attribute) -> None:
        self._module_chain_as_value(node)
        if node.attr == "DatabaseWrapper" and _root(node) in self.roots:
            self.flag(
                node,
                "Django's real `DatabaseWrapper` may only be imported as `BaseWrapper` by the "
                "`_django` backend modules (postgresql.py, mysql.py)",
            )
        if node.attr == "_django" and _root(node) in self.package_roots and not self.in_django:
            self.flag(
                node,
                "reaching `asyncopation._django` through a bound `asyncopation` name hides the "
                "boundary rules - use `from asyncopation._django.<module> import <name>`",
            )
        if node.attr in BANNED_NAMES and _root(node) in self.roots:
            self.flag(
                node,
                f"`.{node.attr}` reached through a Django import - Django's sync "
                "connection/transaction machinery must not be used",
            )
        if node.attr in {"__dict__", "__getattribute__"} and _root(node) in self.roots:
            self.flag(node, f"`.{node.attr}` reaches Django's connection machinery")
        if _dotted(node) == CONNECTIONS_TARGET and not (self.importer or self.exempt):
            self.flag(
                node, "`asyncopation._django.connections` is reachable only from compile/scope.py"
            )
        if _dotted(node) == ADAPTER_TARGET and not self.in_pg_driver:
            self.flag(
                node,
                "`asyncopation._django.postgresql_adapters` may only be used by "
                "asyncopation/drivers/postgresql",
                "A104",
            )
        if node.attr in SWAP_NAMES and not (self.importer or self.exempt):
            self.flag(node, f"`.{node.attr}` is private to compile/scope.py (ADR-0008, Decision 6)")
        if node.attr == "BaseWrapper" and not (self.in_compile or self.in_django):
            self.flag(node, "Django's real PostgreSQL wrapper class is for compile/ only")

    def _module_getattr_as_value(self, node: ast.Call) -> None:
        """``getattr(models, "base")`` is a Django module object just like ``models.base``."""
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            return
        base = self._module_path_of(node.args[0])
        if base is None or not self.is_module(base, node.args[1].value):
            return
        parent = self.parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return
        self.flag(node, f"Django module `{base}.{node.args[1].value}` used as a value")

    def _module_path_of(self, node: ast.AST) -> str | None:
        return module_path_of(node, self.module_paths, self.is_module)

    def _getattr_call(self, node: ast.Call) -> None:
        self._module_getattr_as_value(node)
        if not (
            isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
        ):
            return
        constant = isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
        if not constant:
            if _root(node.args[0]) in self.roots and _peeked_name(node) is None:
                self.flag(node, "`getattr` with a computed name on a Django object")
            if _root(node.args[0]) in self.package_roots:
                self.flag(node, "`getattr` with a computed name on an `asyncopation` object")
            return
        name = node.args[1].value
        if name == "_django" and _root(node.args[0]) in self.package_roots and not self.in_django:
            self.flag(
                node,
                "reaching `asyncopation._django` through a bound `asyncopation` name hides the "
                "boundary rules - use `from asyncopation._django.<module> import <name>`",
            )
        if name in BANNED_NAMES and _root(node.args[0]) in self.roots:
            self.flag(node, f"`getattr(..., {name!r})` reaches Django's connection machinery")
        if name in SWAP_NAMES and not (self.importer or self.exempt):
            self.flag(node, f"`getattr(..., {name!r})` reaches the private swap")
        if name == "DatabaseWrapper" and _root(node.args[0]) in self.roots:
            self.flag(
                node,
                "Django's real `DatabaseWrapper` may only be imported as `BaseWrapper` by the "
                "`_django` backend modules (postgresql.py, mysql.py)",
            )
        if name == "BaseWrapper" and not (self.in_compile or self.in_django):
            self.flag(node, "Django's real PostgreSQL wrapper class is for compile/ only")

    # the exempt module -----------------------------------------------------------------------
    def _swap_functions(self) -> None:
        """Only the ``swapped`` context manager may use ``connections`` (no returned setters)."""
        for func in ast.walk(self.tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            uses = [n for n in _own_nodes(func) if _is_name(n, "connections")]
            decorated = any(_is_name(d, "contextmanager") for d in func.decorator_list)
            if uses and not (func.name == "swapped" and decorated):
                self.flag(
                    func,
                    f"`{func.name}` uses `connections`: only the `swapped` context manager may",
                )

    def _exempt_module(self) -> None:
        tree = self.tree
        parents = self.parents
        peeked: dict[str, str | None] = {}  # peeked name -> key name it was peeked with
        keys: set[str] = set()
        allowed: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.AsyncFunctionDef | ast.Await | ast.AsyncWith | ast.AsyncFor):
                self.flag(
                    n, "the swap module must stay synchronous (the swap never spans an await)"
                )
        self._swap_functions()
        for n in ast.walk(tree):
            target = _single_name_target(n)
            value = getattr(n, "value", None)
            if target is not None and (name := _peeked_name(value)) is not None:
                allowed.add(id(name))
                key = value.args[1] if isinstance(value, ast.Call) and len(value.args) > 1 else None
                peeked[target] = key.id if isinstance(key, ast.Name) else None
                if isinstance(key, ast.Name):
                    keys.add(key.id)
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Subscript)
                and isinstance(n.ctx, ast.Store | ast.Del)
                and not isinstance(parents.get(id(n)), ast.AugAssign)
                and _is_name(n.value, "connections")
                and isinstance(n.slice, ast.Name)
                and n.slice.id in keys
            ):
                allowed.add(id(n.value))
        for n in ast.walk(tree):
            if _is_name(n, "connections") and id(n) not in allowed:
                self.flag(
                    n,
                    "only `connections[alias] = ...`, `del connections[alias]` and "
                    "`x = getattr(connections._connections, alias, default)` are allowed here "
                    "(ADR-0008, Decision 6)",
                )
            if isinstance(n, ast.Name) and n.id in peeked and isinstance(n.ctx, ast.Load):
                self._peeked_use(n, parents, peeked[n.id])
            if isinstance(n, ast.Name) and n.id in keys and isinstance(n.ctx, ast.Store | ast.Del):
                self.flag(n, "the swap key must not be reassigned")
            if isinstance(n, ast.Global | ast.Nonlocal) and set(peeked) & set(n.names):
                self.flag(n, "the peeked wrapper must stay a local variable")
            if isinstance(n, ast.Attribute) and n.attr in OPERATIONS:
                self.flag(n, f"`.{n.attr}` is a sync connection operation")
            if isinstance(n, ast.Name) and n.id in _INTROSPECTION:
                self.flag(n, f"`{n.id}` would expose the peeked wrapper")
            if isinstance(n, ast.Attribute) and n.attr in _FRAME_ATTRIBUTES:
                self.flag(n, f"`.{n.attr}` would expose the peeked wrapper")

    def _peeked_use(self, node: ast.Name, parents: dict[int, ast.AST], key: str | None) -> None:
        parent = parents.get(id(node))
        compared = (
            isinstance(parent, ast.Compare)
            and len(parent.ops) == 1
            and isinstance(parent.ops[0], ast.Is | ast.IsNot)
        )
        stored = (
            isinstance(parent, ast.Assign)
            and parent.value is node
            and all(
                isinstance(t, ast.Subscript)
                and _is_name(t.value, "connections")
                and isinstance(t.slice, ast.Name)
                and t.slice.id == key
                for t in parent.targets
            )
        )
        if not (compared or stored):
            self.flag(
                node,
                "the peeked wrapper may only be compared with `is` or stored back as "
                "`connections[<its own key>] = x`",
            )


def _django_roots(tree: ast.Module) -> frozenset[str]:
    """Names bound by Django imports, plus every name assigned from one (aliases, defaults).

    ``import django.db.models`` binds ``django``; ``b = models.base`` and ``def f(m=models)`` make
    ``b`` and ``m`` Django names too (to a fixed point).
    """
    roots = _imported_roots(tree)
    while True:
        found = set(roots)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr) and (
                value := node.value
            ):
                if _root(value) in roots:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    found |= {t.id for t in targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                found |= _parameters_defaulting_to(node, roots)
        if found == roots:
            return frozenset(roots)
        roots = found


def module_paths(tree: ast.Module, is_module: Callable[[str, str], bool]) -> dict[str, str]:
    """Names bound to a Django *module* object, with the module's dotted path.

    ``import django.db`` binds ``django`` -> ``django``; ``import django.db.models as m`` binds
    ``m``; ``from django.db import models`` binds ``models`` when ``django.db.models`` is a module.
    """
    paths: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "django":
                    paths[a.asname or a.name.split(".")[0]] = a.name if a.asname else "django"
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] == "django"
        ):
            for a in node.names:
                if a.name != "*" and is_module(node.module or "", a.name):
                    paths[a.asname or a.name] = f"{node.module}.{a.name}"
    return paths


def module_path_of(
    node: ast.AST, paths: dict[str, str], is_module: Callable[[str, str], bool]
) -> str | None:
    """Dotted path when ``node`` is a Django module: a bound name or an attribute chain of one.

    A constant ``getattr`` step (``getattr(pkg, "sub").name``) is followed like an attribute.
    """
    if isinstance(node, ast.Name):
        return paths.get(node.id)
    if isinstance(node, ast.Attribute):
        base = module_path_of(node.value, paths, is_module)
        if base is not None and is_module(base, node.attr):
            return f"{base}.{node.attr}"
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        base = module_path_of(node.args[0], paths, is_module)
        if base is not None and is_module(base, node.args[1].value):
            return f"{base}.{node.args[1].value}"
    return None


def _own_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Nodes of ``func`` excluding the bodies of functions nested inside it."""
    found: list[ast.AST] = []
    stack: list[ast.AST] = list(func.body)
    while stack:
        node = stack.pop()
        found.append(node)
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))
    return found


def _asyncopation_roots(tree: ast.Module) -> frozenset[str]:
    """Names bound by ``import asyncopation[.x] [as a]`` (not by ``from`` imports)."""
    return frozenset(
        a.asname or a.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for a in node.names
        if a.name.split(".")[0] == "asyncopation"
    )


def _parameters_defaulting_to(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, roots: set[str]
) -> set[str]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    pairs = list(
        zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True)
    )
    pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d]
    return {a.arg for a, default in pairs if _root(default) in roots}


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {
                a.asname or a.name.split(".")[0]
                for a in node.names
                if a.name.split(".")[0] == "django"
            }
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] == "django"
        ):
            roots |= {a.asname or a.name for a in node.names if a.name != "*"}
    return roots


def _resolve(path: str, node: ast.ImportFrom) -> str:
    """Absolute module of a ``from`` import (relative levels resolved against ``path``)."""
    if not node.level:
        return node.module or ""
    package = PurePosixPath(path).parts[1:-1]  # drop "src" and the file name
    kept = package[: len(package) - (node.level - 1)]
    return ".".join([*kept, *([node.module] if node.module else [])])


def _class_name(node: ast.AST) -> str:
    """Name of a base class or callee: ``X`` or the last attribute of ``m.X``."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _forwards_wrapper(node: ast.AST) -> bool:
    """Whether the expression evaluates to ``BaseWrapper`` itself (name, attribute, container)."""
    if isinstance(node, ast.Name):
        return node.id == "BaseWrapper"
    if isinstance(node, ast.Attribute):
        return node.attr == "BaseWrapper"
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return any(_forwards_wrapper(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return any(_forwards_wrapper(v) for v in node.values)
    if isinstance(node, ast.Starred | ast.NamedExpr):
        return _forwards_wrapper(node.value)
    if isinstance(node, ast.IfExp):
        return _forwards_wrapper(node.body) or _forwards_wrapper(node.orelse)
    if isinstance(node, ast.BoolOp):
        return any(_forwards_wrapper(v) for v in node.values)
    return False


def _is_backend_base(module: str) -> bool:
    """``django[.contrib.gis].db.backends.<vendor>.base``: where a ``DatabaseWrapper`` lives."""
    return module.endswith(".base") and module.startswith(
        ("django.db.backends.", "django.contrib.gis.db.backends.")
    )


def _under(module: str, target: str) -> bool:
    return module == target or module.startswith(target + ".")


def _aliased(node: ast.ImportFrom) -> bool:
    return any(a.asname for a in node.names)


def _root(node: ast.AST) -> str | None:
    """Root name of an attribute chain, looking through ``getattr(x, "name")`` calls."""
    while True:
        if isinstance(node, ast.Attribute | ast.NamedExpr):
            node = node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
        ):
            node = node.args[0]
        else:
            break
    return node.id if isinstance(node, ast.Name) else None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _single_name_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def _peeked_name(node: ast.AST | None) -> ast.Name | None:
    """The ``connections`` name in ``getattr(connections._connections, ...)``, else ``None``."""
    if not (
        isinstance(node, ast.Call)
        and _is_name(node.func, "getattr")
        and node.args
        and not node.keywords
    ):
        return None
    first = node.args[0]
    if (
        isinstance(first, ast.Attribute)
        and first.attr == "_connections"
        and isinstance(first.ctx, ast.Load)
        and isinstance(first.value, ast.Name)
        and first.value.id == "connections"
    ):
        return first.value
    return None
