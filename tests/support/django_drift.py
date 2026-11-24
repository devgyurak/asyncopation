"""Drift checks for docs/django-api-ledger.toml against the installed Django.

Each function returns a human-readable problem (None = no drift) and never raises, so tests stay
free of control flow.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import textwrap
from pathlib import Path
from typing import Any

PINNED_DJANGO = (6, 1)


def import_problem(module: str, name: str) -> str | None:
    """Why ``module.name`` cannot be imported, or None. Name ``*`` checks the module only."""
    try:
        target = importlib.import_module(module)
    except Exception as exc:
        return f"{module}: {type(exc).__name__}: {exc}"
    if name == "*" or hasattr(target, name):
        return None
    return f"{module}.{name}: not found"


def signature_problem(module: str, name: str, expected: str) -> str | None:
    """Why the signature of ``module.<dotted name>`` differs from the recorded one, or None."""
    return _signature_of(f"{module}.{name}", expected)


def attribute_signature_problem(owner: str, name: str, expected: str) -> str | None:
    """Same as :func:`signature_problem` for ``owner.name`` (owner is a dotted class path)."""
    return _signature_of(f"{owner}.{name}", expected)


def attribute_problem(owner: str, name: str) -> str | None:
    """Why ``owner.name`` no longer exists, or None.

    A class attribute passes ``hasattr``; an instance attribute is found as a ``self.<name>``
    assignment in the source of the owner or any base class.
    """
    cls = _resolve(owner)
    if isinstance(cls, _Unresolved):
        return f"{owner}: owner not resolvable ({cls.reason})"
    if not inspect.isclass(cls):
        return f"{owner}: owner is not a class"
    if hasattr(cls, name) or any(_assigns_self_attribute(base, name) for base in cls.__mro__):
        return None
    return f"{owner}.{name}: not a class attribute and never assigned as self.{name}"


def ported_problem(module: str, name: str) -> str | None:
    """Why a *ported* (never imported) ``module.<dotted name>`` is gone from the source."""
    try:
        spec = importlib.util.find_spec(module)
        source = Path(spec.origin).read_text() if spec and spec.origin else None
    except Exception as exc:
        return f"{module}: {type(exc).__name__}: {exc}"
    if source is None:
        return f"{module}: no source file"
    if _defines(ast.parse(source).body, name.split(".")):
        return None
    return f"{module}.{name}: not defined in the module source"


class _Unresolved:
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _resolve(dotted: str) -> Any:
    """Import the longest importable module prefix, then walk the rest with ``getattr``."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        except Exception as exc:
            return _Unresolved(f"{type(exc).__name__}: {exc}")
        for attr in parts[cut:]:
            if not hasattr(obj, attr):
                return _Unresolved(f"{attr} not found")
            obj = getattr(obj, attr)
        return obj
    return _Unresolved("not importable")


def _signature_of(dotted: str, expected: str) -> str | None:
    target = _resolve(dotted)
    if isinstance(target, _Unresolved):
        return f"{dotted}: signature unavailable ({target.reason})"
    try:
        actual = str(inspect.signature(target))
    except (TypeError, ValueError) as exc:
        return f"{dotted}: signature unavailable ({exc})"
    if actual != expected:
        return f"{dotted}: signature is {actual}, ledger records {expected}"
    return None


def _defines(body: list[ast.stmt], path: list[str]) -> bool:
    head, rest = path[0], path[1:]
    for node in body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name == head:
                return not rest or (isinstance(node, ast.ClassDef) and _defines(node.body, rest))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not rest and any(isinstance(t, ast.Name) and t.id == head for t in targets):
                return True
    return False


def _assigns_self_attribute(cls: Any, name: str) -> bool:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    except (OSError, TypeError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        for node in ast.walk(tree)
    )
