"""Compare the ported MySQL wrapper with the *source* of Django's ``mysql/base.py`` (no MySQLdb).

Django's module imports ``MySQLdb``, so it is never imported here: it is located, read and parsed
(``ast``), which works wherever Django is installed. The behavioural comparison with the real class
lives in ``mysql_drift`` and needs mysqlclient.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
from pathlib import Path
from typing import Any

from asyncopation.compile import mysql
from asyncopation.compile.server_info import MySQLServerInfo
from tests.support.mysql_drift import EXPECTED_PORTED

# Set to Django's own classes (or a stand-in) by composition, not copied.
COMPOSED: frozenset[str] = frozenset(
    {
        "Database",
        "client_class",
        "creation_class",
        "features_class",
        "introspection_class",
        "ops_class",
    }
)
# Members of Django's `DatabaseWrapper` that are deliberately not ported (compile-only): the
# compile-only mixin refuses or never needs them. A new Django member lands in neither set and
# fails `test_every_django_wrapper_member_is_ported_composed_or_deliberately_left_out`.
NOT_PORTED: frozenset[str] = frozenset(
    {
        "SchemaEditorClass",
        "validation_class",
        "get_connection_params",
        "get_new_connection",
        "init_connection_state",
        "create_cursor",
        "_rollback",
        "_set_autocommit",
        "disable_constraint_checking",
        "enable_constraint_checking",
        "check_constraints",
        "is_usable",
    }
)
# Ported verbatim: their AST must equal Django's. `mysql_version` differs only in the message of the
# `raise` (an f-string instead of `%`), so it is compared without its `if` statement.
SAME_FUNCTIONS = (
    "data_types",
    "get_database_version",
    "display_name",
    "data_type_check_constraints",
    "mysql_server_info",
    "mysql_is_mariadb",
    "sql_mode",
)
SAME_ASSIGNMENTS = (
    "vendor",
    "_data_types",
    "_limited_data_types",
    "operators",
    "pattern_esc",
    "pattern_ops",
    "isolation_levels",
)
BASE_MODULE = "django.db.backends.mysql.base"
BRIDGE_MODULE = "asyncopation._django.mysql"
# The `*_class` attributes that are Django's own classes; `introspection_class` is a stand-in.
COMPOSED_CLASS_ATTRIBUTES = ("client_class", "creation_class", "features_class", "ops_class")


def django_tree() -> ast.Module:
    spec = importlib.util.find_spec(BASE_MODULE)
    assert spec is not None
    assert spec.origin is not None
    return ast.parse(Path(spec.origin).read_text())


def our_tree() -> ast.Module:
    return ast.parse(Path(inspect.getfile(mysql)).read_text())


def wrapper_class(tree: ast.Module) -> ast.ClassDef:
    names = ("DatabaseWrapper", "MySQLCompileWrapper")
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names)


def _target_names(target: ast.expr) -> set[str]:
    """The names an assignment target binds: a name, or the names of a tuple / list of targets.

    Anything else (an attribute, a subscript, a starred target) is not a class member Django declares
    this way; it raises so a new form is looked at instead of silently ignored.
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return set().union(*(_target_names(element) for element in target.elts))
    raise ValueError(f"unsupported assignment target in a class body: {ast.dump(target)}")


def _statement_lists(node: ast.stmt) -> list[list[ast.stmt]]:
    """The nested statement lists of a compound statement (``if``, ``try``, ``with``, loops, ``match``)."""
    if isinstance(node, ast.If | ast.For | ast.AsyncFor | ast.While):
        return [node.body, node.orelse]
    if isinstance(node, ast.Try | ast.TryStar):
        return [
            node.body,
            *(handler.body for handler in node.handlers),
            node.orelse,
            node.finalbody,
        ]
    if isinstance(node, ast.With | ast.AsyncWith):
        return [node.body]
    if isinstance(node, ast.Match):
        return [case.body for case in node.cases]
    return []


def _body_member_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(*(_target_names(target) for target in node.targets))
        elif isinstance(node, ast.AnnAssign):
            names.update(_target_names(node.target))
        else:
            for nested in _statement_lists(node):
                names |= _body_member_names(nested)
    return names


def member_names(cls: ast.ClassDef) -> set[str]:
    """Every name the class body binds, also inside a class-level ``if`` / ``try`` / ``with``."""
    return _body_member_names(cls.body)


def django_members() -> set[str]:
    return member_names(wrapper_class(django_tree()))


def accounted_members() -> set[str]:
    """What the ledger, the composition and the explicit list of omissions account for."""
    return set(EXPECTED_PORTED) | COMPOSED | NOT_PORTED


def unaccounted_members() -> set[str]:
    return django_members() - accounted_members()


def stale_accounted_members() -> set[str]:
    return accounted_members() - django_members()


def _function(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _value(cls: ast.ClassDef, name: str) -> ast.expr | None:
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return node.value
    return None


def _dump(node: Any) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def differing_functions() -> list[str]:
    """Names of the ported methods whose AST (decorators included) differs from Django's."""
    ours, theirs = wrapper_class(our_tree()), wrapper_class(django_tree())
    return [
        name
        for name in SAME_FUNCTIONS
        if _dump(_function(ours, name)) != _dump(_function(theirs, name))
    ]


def differing_assignments() -> list[str]:
    ours, theirs = wrapper_class(our_tree()), wrapper_class(django_tree())
    return [
        name
        for name in SAME_ASSIGNMENTS
        if _dump(_value(ours, name)) != _dump(_value(theirs, name))
    ]


def _if_shape(node: ast.If) -> list[str]:
    """An ``if``: its test and the exception type it raises (the message is excluded)."""
    raised = [
        n.exc.func if isinstance(n.exc, ast.Call) else n.exc
        for n in node.body
        if isinstance(n, ast.Raise) and n.exc is not None
    ]
    return [
        "if",
        _dump(node.test),
        *(_dump(exc) for exc in raised),
        str(len(node.body)),
        str(len(node.orelse)),
    ]


def version_statements(function: ast.FunctionDef) -> list[str]:
    """The statements of ``mysql_version``; an ``if`` is reduced to its test and exception type."""
    body = [_if_shape(n) if isinstance(n, ast.If) else _dump(n) for n in function.body]
    return [*map(repr, body), *(_dump(n) for n in function.decorator_list)]


def _version_statements(tree: ast.Module) -> list[str]:
    return version_statements(_function(wrapper_class(tree), "mysql_version"))


def mysql_version_differs() -> bool:
    return _version_statements(our_tree()) != _version_statements(django_tree())


def _is_bool_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bool"
        and len(node.args) == 1
    )


def _string_key(key: ast.expr | None) -> str:
    """A dict literal key that is a string constant; a ``**`` spread or computed key raises."""
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    raise ValueError(
        f"unsupported dict key (** spread or computed): {ast.dump(key) if key else '**'}"
    )


def dict_shape(node: ast.Dict) -> dict[str, bool]:
    """``{key: whether the value is wrapped in bool(...)}`` of a dict literal with string keys.

    A key built by a ``**`` spread (or any non-string-constant key) raises, so that a Django change
    that hides keys from this comparison is looked at instead of dropped.
    """
    return {
        _string_key(key): _is_bool_call(value)
        for key, value in zip(node.keys, node.values, strict=True)
    }


def _unwrap_bool(value: ast.expr) -> ast.expr:
    """``x`` of ``bool(x)``; any other expression as it is."""
    return value.args[0] if isinstance(value, ast.Call) and _is_bool_call(value) else value


def _row_index(value: ast.expr) -> int:
    """``n`` of ``row[n]`` or ``bool(row[n])``; anything else raises."""
    inner = _unwrap_bool(value)
    if (
        isinstance(inner, ast.Subscript)
        and isinstance(inner.slice, ast.Constant)
        and isinstance(inner.slice.value, int)
    ):
        return inner.slice.value
    raise ValueError(f"expected row[n] or bool(row[n]): {ast.dump(value)}")


def row_indexes(node: ast.Dict) -> dict[str, int]:
    """``{key: n}`` for a dict literal whose values are ``row[n]`` / ``bool(row[n])``."""
    return {
        _string_key(key): _row_index(value)
        for key, value in zip(node.keys, node.values, strict=True)
    }


def _attribute_name(value: ast.expr) -> str:
    """``name`` of ``info.name`` or ``bool(info.name)``; anything else raises."""
    inner = _unwrap_bool(value)
    if isinstance(inner, ast.Attribute):
        return inner.attr
    raise ValueError(f"expected info.name or bool(info.name): {ast.dump(value)}")


def injected_attributes(node: ast.Dict) -> dict[str, str]:
    """``{key: attribute of info}`` of the dict `apply_server_info` injects."""
    return {
        _string_key(key): _attribute_name(value)
        for key, value in zip(node.keys, node.values, strict=True)
    }


def _returned_dict(function: ast.FunctionDef) -> ast.Dict:
    return next(
        n.value
        for n in ast.walk(function)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
    )


def _injected_dict(function: ast.FunctionDef) -> ast.Dict:
    """The dict literal `apply_server_info` passes to ``inject_mysql_server_data``."""
    calls = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "inject_mysql_server_data"
    ]
    return next(arg for call in calls for arg in call.args if isinstance(arg, ast.Dict))


def django_server_data_shape(tree: ast.Module | None = None) -> dict[str, bool]:
    function = _function(wrapper_class(tree or django_tree()), "mysql_server_data")
    return dict_shape(_returned_dict(function))


def our_server_data_shape(tree: ast.Module | None = None) -> dict[str, bool]:
    function = _function(wrapper_class(tree or our_tree()), "apply_server_info")
    return dict_shape(_injected_dict(function))


def django_row_indexes(tree: ast.Module | None = None) -> dict[str, int]:
    """Which ``row[n]`` of Django's ``SELECT VERSION(), @@sql_mode, ...`` feeds each key."""
    function = _function(wrapper_class(tree or django_tree()), "mysql_server_data")
    return row_indexes(_returned_dict(function))


def our_injected_attributes(tree: ast.Module | None = None) -> dict[str, str]:
    function = _function(wrapper_class(tree or our_tree()), "apply_server_info")
    return injected_attributes(_injected_dict(function))


def row_order_pair(tree: ast.Module | None = None) -> tuple[list[str], list[str]]:
    """The keys in Django's ``row[n]`` order and the field order of ``MySQLServerInfo``.

    The #7 probe builds a ``MySQLServerInfo`` from the same ``SELECT`` row, so the dataclass field
    order is the row order (ADR-0008 Decision 5).
    """
    indexes = django_row_indexes(tree)
    by_index = [key for key, _ in sorted(indexes.items(), key=lambda item: item[1])]
    return by_index, [field.name for field in dataclasses.fields(MySQLServerInfo)]


def row_index_coverage_pair(tree: ast.Module | None = None) -> tuple[list[int], list[int]]:
    """Django's ``row[n]`` indexes (sorted) and ``0..n-1`` for the fields of ``MySQLServerInfo``.

    Equal means the probe can build the info positionally from the row: field ``n`` is ``row[n]``.
    """
    indexes = sorted(django_row_indexes(tree).values())
    return indexes, list(range(len(dataclasses.fields(MySQLServerInfo))))


def row_index_coverage_pair_of(source: str) -> tuple[list[int], list[int]]:
    """`row_index_coverage_pair` over a snippet of a wrapper class with ``mysql_server_data``."""
    return row_index_coverage_pair(ast.parse(source))


def injected_attribute_mismatches(tree: ast.Module | None = None) -> dict[str, str]:
    """``{key: attribute}`` of the injected dict entries that read a different ``info`` attribute."""
    return {key: attr for key, attr in our_injected_attributes(tree).items() if key != attr}


def injected_attribute_mismatches_of(source: str) -> dict[str, str]:
    """`injected_attribute_mismatches` over a snippet of a wrapper class with ``apply_server_info``."""
    return injected_attribute_mismatches(ast.parse(source))


def row_order_pair_of(source: str) -> tuple[list[str], list[str]]:
    """`row_order_pair` over a snippet of a wrapper class with ``mysql_server_data``."""
    return row_order_pair(ast.parse(source))


def injected_attributes_of(source: str) -> dict[str, str]:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Dict)
    return injected_attributes(node)


def row_indexes_of(source: str) -> dict[str, int]:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Dict)
    return row_indexes(node)


def server_data_keys_pair() -> tuple[list[str], list[str]]:
    """The keys of the dict Django returns from ``mysql_server_data`` and of the one we inject."""
    return list(django_server_data_shape()), list(our_server_data_shape())


def server_data_bool_keys_pair() -> tuple[list[str], list[str]]:
    """The keys coerced with ``bool(...)`` in Django's query row and in ``apply_server_info``."""
    theirs, ours = django_server_data_shape(), our_server_data_shape()
    return (
        sorted(k for k, v in theirs.items() if v),
        sorted(k for k, v in ours.items() if v),
    )


def _pattern_of(tree: ast.Module) -> str:
    assign = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "server_version_re" for t in n.targets)
    )
    call = assign.value
    assert isinstance(call, ast.Call)
    return _dump(call.args[0])


def version_pattern_pair() -> tuple[str, str]:
    return _pattern_of(our_tree()), _pattern_of(django_tree())


def parsed_class(source: str) -> ast.ClassDef:
    """The first class of `source` (a snippet for exercising the comparison helpers)."""
    return next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))


def parsed_function(source: str) -> ast.FunctionDef:
    return next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef))


def version_statements_of(source: str) -> list[str]:
    return version_statements(parsed_function(source))


def dict_shape_of(source: str) -> dict[str, bool]:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Dict)
    return dict_shape(node)


def member_names_of(source: str) -> set[str]:
    return member_names(parsed_class(source))


VERSION_SOURCE = (
    "def mysql_version(self):\n"
    "    match = pattern.match(self.info)\n"
    "    if not match:\n"
    "        raise Exception({message})\n"
    "    return tuple(int(x) for x in match.groups())\n"
)


def _imported_from(tree: ast.Module, package: str) -> dict[str, str]:
    """``{name: qualified module}`` of the ``from ... import name`` statements of `tree`.

    A relative import is resolved against `package` (``from .features import X`` -> ``package.features``).
    """
    imported: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = f"{package}.{node.module}" if node.level else node.module or ""
            imported.update({(alias.asname or alias.name): module for alias in node.names})
    return imported


def _class_names(cls: ast.ClassDef, attributes: tuple[str, ...]) -> dict[str, str]:
    values = {name: _value(cls, name) for name in attributes}
    return {
        name: value.id if isinstance(value, ast.Name) else ast.dump(value) if value else "<missing>"
        for name, value in values.items()
    }


def djangos_composed_classes(base: ast.Module, package: str) -> dict[str, str]:
    """``{attribute: module.Class}`` for the `COMPOSED_CLASS_ATTRIBUTES` of Django's wrapper."""
    names = _class_names(wrapper_class(base), COMPOSED_CLASS_ATTRIBUTES)
    modules = _imported_from(base, package)
    return {attr: f"{modules.get(name, '?')}.{name}" for attr, name in names.items()}


def our_composed_classes(ours: ast.Module, bridge: ast.Module) -> dict[str, str]:
    """The same for the compile-only wrapper: the class names come through ``_django.mysql``."""
    names = _class_names(wrapper_class(ours), COMPOSED_CLASS_ATTRIBUTES)
    bridge_modules = _imported_from(bridge, "")
    return {attr: f"{bridge_modules.get(name, '?')}.{name}" for attr, name in names.items()}


def _bridge_tree() -> ast.Module:
    spec = importlib.util.find_spec(BRIDGE_MODULE)
    assert spec is not None
    assert spec.origin is not None
    return ast.parse(Path(spec.origin).read_text())


def composed_classes_pair() -> tuple[dict[str, str], dict[str, str]]:
    """Our composed classes and Django's, each as ``{attribute: module.Class}``."""
    package = BASE_MODULE.rpartition(".")[0]
    return (
        our_composed_classes(our_tree(), _bridge_tree()),
        djangos_composed_classes(django_tree(), package),
    )


def composed_classes_pair_of(django_source: str, our_source: str, bridge_source: str):
    """`composed_classes_pair` over snippets (exercising the comparison itself)."""
    package = BASE_MODULE.rpartition(".")[0]
    return (
        our_composed_classes(ast.parse(our_source), ast.parse(bridge_source)),
        djangos_composed_classes(ast.parse(django_source), package),
    )


def decorator_names(function: ast.FunctionDef) -> list[str]:
    return [d.id if isinstance(d, ast.Name) else ast.dump(d) for d in function.decorator_list]


def django_server_data_decorators(tree: ast.Module | None = None) -> list[str]:
    """The decorators of Django's ``mysql_server_data`` (we pre-fill it in ``__dict__``, which only
    works for a non-data descriptor such as ``cached_property``)."""
    return decorator_names(_function(wrapper_class(tree or django_tree()), "mysql_server_data"))


def django_server_data_decorators_of(source: str) -> list[str]:
    return decorator_names(parsed_function(source))


COMPOSED_SOURCES = {
    "django": (
        "from .client import DatabaseClient\nfrom .creation import DatabaseCreation\n"
        "from .features import DatabaseFeatures\nfrom .operations import DatabaseOperations\n"
        "class DatabaseWrapper:\n    client_class = DatabaseClient\n"
        "    creation_class = DatabaseCreation\n    features_class = {features}\n"
        "    ops_class = DatabaseOperations\n"
    ),
    "ours": (
        "class MySQLCompileWrapper:\n    client_class = DatabaseClient\n"
        "    creation_class = DatabaseCreation\n    features_class = DatabaseFeatures\n"
        "    ops_class = DatabaseOperations\n"
    ),
    "bridge": (
        "from django.db.backends.mysql.client import DatabaseClient\n"
        "from django.db.backends.mysql.creation import DatabaseCreation\n"
        "from django.db.backends.mysql.features import DatabaseFeatures\n"
        "from django.db.backends.mysql.operations import DatabaseOperations\n"
    ),
}


APPLY_SERVER_INFO_SOURCE = (
    "class MySQLCompileWrapper:\n"
    "    def apply_server_info(self, info):\n"
    "        inject_mysql_server_data(self, {entries})\n"
)
SERVER_DATA_SOURCE = (
    "class DatabaseWrapper:\n"
    "    def mysql_server_data(self):\n"
    "        row = None\n"
    "        return {{\n"
    "            'version': row[0],\n"
    "            'sql_mode': row[{sql_mode}],\n"
    "            'default_storage_engine': row[{engine}],\n"
    "            'sql_auto_is_null': bool(row[3]),\n"
    "            'lower_case_table_names': bool(row[4]),\n"
    "            'has_zoneinfo_database': bool(row[5]),\n"
    "        }}\n"
)
